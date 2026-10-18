#include <gtest/gtest.h>

#include <limits>

#include "analog.hpp"
#include "control_features.hpp"
#include "dfu.hpp"
#include "host_adc.hpp"
#include "lhal/host/pwm.hpp"
#include "rgb_led.hpp"
#include "torque.hpp"

namespace {

TEST(Dfu, MatchesRawLegacyTokenAcrossPacketsWithoutNewline) {
  vcu::DfuCommand command;
  for (uint8_t byte : {'u', 'p', 'x', 'u', 'u', 'p', 'd', 'a', 't', 'e'}) {
    EXPECT_FALSE(command.Feed(byte));
  }
  EXPECT_TRUE(command.Feed('.'));
  EXPECT_FALSE(command.Feed('\n'));
}

TEST(Rainbow, PreservesLegacyPwmBrightnessAndFiveSecondColorCycle) {
  lhal::host::Pwm red, green, blue;
  const float peak = 127.0f / 1001.0f;
  vcu::RgbLed led(&red, &green, &blue, peak, 125.0f / 1001.0f);
  EXPECT_FLOAT_EQ(red.duty(), 125.0f / 1001.0f);
  led.Rainbow(0);
  EXPECT_FLOAT_EQ(red.duty(), peak);
  led.Rainbow(5.0f / 3.0f);
  EXPECT_NEAR(green.duty(), peak, 1e-6f);
  led.Rainbow(5.0f / 3.0f);
  EXPECT_NEAR(blue.duty(), peak, 1e-6f);
  led.Rainbow(5.0f / 3.0f);
  EXPECT_NEAR(red.duty(), peak, 1e-6f);
}

TEST(WheelSlip, OneWheelSpinCutsTorqueAndRecoveryIsLimited) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  p.enabled = true;
  vcu::WheelInputs in;
  in.fresh = true;
  for (float& speed : in.rad_s) speed = 50;
  vcu::WheelControlResult out;
  for (int i = 0; i < 200; ++i) out = control.Update(in, 100, 3, p);
  ASSERT_NEAR(out.positive_nm, 100, 0.01f);
  in.rad_s[2] = 150;  // other rear wheel still rolls normally
  out = control.Update(in, 100, 3, p);
  EXPECT_EQ(out.positive_nm, 0);
  in.rad_s[2] = 50;
  out = control.Update(in, 100, 3, p);
  EXPECT_LE(out.positive_nm, p.recovery_nm_s * 0.003f);
  in.fresh = false;
  EXPECT_EQ(control.Update(in, 100, 3, p).positive_nm, 0);
}

TEST(WheelSlip, LockedRearWheelBlocksRegenAndZeroSpeedIsFinite) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  vcu::WheelInputs in;
  in.fresh = true;
  auto out = control.Update(in, 0, 3, p);
  EXPECT_TRUE(out.valid);
  EXPECT_EQ(out.drive_slip, 0);
  EXPECT_EQ(out.braking_slip, 0);
  for (float& speed : in.rad_s) speed = 50;
  in.rad_s[3] = 20;
  EXPECT_FALSE(control.Update(in, 0, 3, p).regen_allowed);
  in.rad_s[0] = -1;
  EXPECT_FALSE(control.Update(in, 0, 3, p).valid);
}

// Synthetic commissioned limits for tests; never production pack permission.
vcu::RegenParams TestRegenParams() {
  vcu::RegenParams p;
  p.enabled = true;
  p.max_current_a = 45;
  p.hard_cut_current_a = 54;
  p.max_pack_v = 546;
  p.pack_resistance_ohm = 0.442f;
  return p;
}

vcu::RegenInputs HealthyRegen() {
  vcu::RegenInputs in;
  in.drive = in.feedback_fresh = in.linelock_out = true;
  in.motor_rpm = 1000;
  in.dc_v = 500;
  in.max_cell_v = 3.9f;
  in.dt_ms = 10;
  return in;
}

void CloseValve(vcu::BrakeRegen& regen, vcu::RegenInputs& in,
                const vcu::RegenParams& p) {
  for (int i = 0; i < 21; ++i) regen.Update(in, p);
}

TEST(Regen, DisabledByDefaultAndCannotEngageDuringExistingBraking) {
  vcu::BrakeRegen regen;
  auto in = HealthyRegen();
  vcu::RegenParams p;
  EXPECT_FALSE(regen.Update(in, p).linelock_request);
  p = TestRegenParams();
  in.rear_pre_psi = 500;
  EXPECT_FALSE(regen.Update(in, p).linelock_request);
  in.rear_pre_psi = 0;
  in.rear_post_psi = 100;
  EXPECT_FALSE(regen.Update(in, p).linelock_request);
}

TEST(Regen, EnablingAloneCannotUseUnknownPackLimits) {
  auto in = HealthyRegen();
  vcu::RegenParams defaults;
  defaults.enabled = true;
  vcu::BrakeRegen uncommissioned;
  EXPECT_FALSE(uncommissioned.Update(in, defaults).linelock_request);
  for (int missing = 0; missing < 3; ++missing) {
    auto p = TestRegenParams();
    if (missing == 0) p.max_current_a = 0;
    if (missing == 1) p.hard_cut_current_a = 0;
    if (missing == 2) p.pack_resistance_ohm = 0;
    vcu::BrakeRegen regen;
    EXPECT_FALSE(regen.Update(in, p).linelock_request) << missing;
  }
}

TEST(Regen, WaitsForGateAndDelayThenSubtractsHydraulicBraking) {
  vcu::BrakeRegen regen;
  auto in = HealthyRegen();
  auto p = TestRegenParams();
  in.linelock_out = false;
  CloseValve(regen, in, p);
  EXPECT_EQ(regen.Update(in, p).torque_nm, 0);
  in.linelock_out = true;
  EXPECT_EQ(regen.Update(in, p).torque_nm, 0);
  in.rear_pre_psi = 500;
  for (int i = 0; i < 18; ++i) EXPECT_EQ(regen.Update(in, p).torque_nm, 0);
  EXPECT_NEAR(regen.Update(in, p).torque_nm, -67, 0.01f);
  in.rear_post_psi = 250;
  EXPECT_NEAR(regen.Update(in, p).torque_nm, -33.5f, 0.01f);
  in.rear_post_psi = 600;
  EXPECT_EQ(regen.Update(in, p).torque_nm, 0);
}

TEST(Regen, EveryLossOfEligibilityOpensValveAndStopsNegativeTorque) {
  for (int fault = 0; fault < 11; ++fault) {
    vcu::BrakeRegen regen;
    auto in = HealthyRegen();
    auto p = TestRegenParams();
    CloseValve(regen, in, p);
    in.rear_pre_psi = 500;
    ASSERT_LT(regen.Update(in, p).torque_nm, 0);
    if (fault == 0) in.hard_braking = true;
    if (fault == 1) in.feedback_fresh = false;
    if (fault == 2) in.motor_rpm = 100;
    if (fault == 3) in.max_cell_v = 4.1f;
    if (fault == 4) in.dc_v = 547;
    if (fault == 5) in.dc_a = -55;
    if (fault == 6) in.pedal = 0.5f;
    if (fault == 7) in.drive = false;
    if (fault == 8) in.dt_ms = 100;
    if (fault == 9) in.rear_post_psi = std::numeric_limits<float>::quiet_NaN();
    if (fault == 10) in.linelock_out = false;
    const auto out = regen.Update(in, p);
    EXPECT_FALSE(out.linelock_request) << fault;
    EXPECT_EQ(out.torque_nm, 0) << fault;
    // Recovery while still braking must not re-isolate the rear axle.
    in = HealthyRegen();
    in.rear_pre_psi = 500;
    EXPECT_FALSE(regen.Update(in, p).linelock_request) << fault;
  }
}

TEST(Regen, RespectsPackPowerAndHardCurrentLatch) {
  vcu::BrakeRegen regen;
  auto in = HealthyRegen();
  auto p = TestRegenParams();
  CloseValve(regen, in, p);
  in.motor_rpm = 6000;
  in.rear_pre_psi = 2000;
  const float max_nm = 500.0f * 45.0f / (6000.0f * 0.104719755f);
  EXPECT_NEAR(regen.Update(in, p).torque_nm, -max_nm, 0.01f);
  in.dc_a = -55;
  EXPECT_TRUE(regen.Update(in, p).current_cut);
  in.dc_a = 0;
  EXPECT_TRUE(regen.Update(in, p).current_cut);
  in.rear_pre_psi = 0;
  EXPECT_FALSE(regen.Update(in, p).current_cut);
}

TEST(Launch, RequiresExplicitStationaryBrakedArmAndCapsNormalDemand) {
  vcu::LaunchRamp launch;
  vcu::LaunchParams p;
  EXPECT_FALSE(launch.Arm(true, true, 0, 0, p));
  p.enabled = true;
  EXPECT_FALSE(launch.Arm(false, true, 0, 0, p));
  EXPECT_FALSE(launch.Arm(true, false, 0, 0, p));
  EXPECT_FALSE(launch.Arm(true, true, 0.5f, 0, p));
  EXPECT_FALSE(launch.Arm(true, true, 0, 100, p));
  ASSERT_TRUE(launch.Arm(true, true, 0, 0, p));
  EXPECT_EQ(launch.Update(200, true, true, 0.5f, 0, 10, p), 0);
  EXPECT_NEAR(launch.Update(200, true, false, 0.5f, 0, 10, p), 31, 0.01f);
  EXPECT_FALSE(launch.Arm(true, true, 0, 0, p))
      << "re-arm must not remove a running cap";
  EXPECT_LE(launch.Update(5, true, false, 0.5f, 0, 10, p), 5);
}

TEST(Launch, BrakeAndTimeoutAbortUntilPedalReleased) {
  for (bool timeout : {false, true}) {
    vcu::LaunchRamp launch;
    vcu::LaunchParams p;
    p.enabled = true;
    if (timeout) p.timeout_ms = 20;
    ASSERT_TRUE(launch.Arm(true, true, 0, 0, p));
    launch.Update(200, true, false, 0.8f, 0, 10, p);
    EXPECT_EQ(launch.Update(200, true, !timeout, 0.8f, 0, 10, p), 0);
    EXPECT_EQ(launch.Update(200, true, false, 0.8f, 0, 10, p), 0);
    EXPECT_EQ(launch.Update(0, true, false, 0, 0, 10, p), 0);
    EXPECT_EQ(launch.Update(100, true, false, 0.5f, 0, 10, p), 100);
  }
}

TEST(Traction, SampleTimingFilterAndHeldTrimSurviveRepeatedControlTicks) {
  const auto& p = vcu::ParamsFor(vcu::kDefaultEventMode);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.motor_speed_valid = true;
  in.pedal_travel = 1;
  in.dt_ms = 3;
  in.speed_sample_ms = 100;
  vcu::TractionParams tc;
  tc.enabled = true;
  tc.accel_filter_alpha = 0.1f;
  path.Update(in, p.torque, p.power_limit, tc);
  in.motor_rpm = 10;
  in.speed_sample_ms = 110;
  const auto first = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_LT(first.traction, first.power_limited);
  for (int i = 0; i < 3; ++i) {
    EXPECT_FLOAT_EQ(path.Update(in, p.torque, p.power_limit, tc).traction,
                    first.traction);
  }
  // A new unchanged speed is zero raw acceleration. The filtered value
  // stays above threshold; using raw acceleration would drop trim to zero.
  in.speed_sample_ms = 120;
  const auto filtered = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_LT(filtered.traction, filtered.power_limited);
  in.motor_speed_valid = false;
  const auto stale = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_EQ(stale.traction, stale.power_limited);
}

TEST(Adc, ConstantValuesAreFreshButFrozenAcquisitionIsNot) {
  vcu::host::AdcScan adc(1);
  vcu::AdcProgress progress;
  EXPECT_FALSE(progress.Fresh(&adc, 0));
  adc.CompleteScan();
  EXPECT_TRUE(progress.Fresh(&adc, 1));
  EXPECT_TRUE(progress.Fresh(&adc, 10));
  EXPECT_FALSE(progress.Fresh(&adc, 11));
  adc.CompleteScan();
  EXPECT_TRUE(progress.Fresh(&adc, 12));
  EXPECT_FALSE(progress.Fresh(nullptr, 22));
}

}  // namespace
