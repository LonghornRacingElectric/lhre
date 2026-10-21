// Unit tests for the VCU's pure logic: conversions, plausibility,
// debouncing, shutdown loop interpretation, torque chain, battery, buzzer.

#include <gtest/gtest.h>

#include "analog.hpp"
#include "battery.hpp"
#include "buzzer.hpp"
#include "faults.hpp"
#include "host_adc.hpp"
#include "longhorn/debounce.hpp"
#include "lookup.hpp"
#include "pedals.hpp"
#include "shutdown_loop.hpp"
#include "steering.hpp"
#include "torque.hpp"
#include "vcu_config.hpp"
#include "vcu_io.hpp"

namespace {

using lhre::can::ShutdownLoopState;
using vcu::Params;

const Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

// Analog
TEST(Analog, VddaFromVrefint) {
  bool from = false;
  // 3.0 V x 1650 / 1500 = 3.3 V.
  EXPECT_NEAR(vcu::ComputeVdda(1500, 1650, 4095, P().analog, &from), 3.3f,
              1e-4f);
  EXPECT_TRUE(from);
  // A sagging rail reads higher VREFINT counts.
  EXPECT_NEAR(vcu::ComputeVdda(1650, 1650, 4095, P().analog, &from), 3.0f,
              1e-4f);
  // Missing calibration or reading falls back to nominal.
  EXPECT_EQ(vcu::ComputeVdda(1500, 0, 4095, P().analog, &from), 3.3f);
  EXPECT_FALSE(from);
  EXPECT_EQ(vcu::ComputeVdda(0, 1650, 4095, P().analog, &from), 3.3f);
  // Implausible result (e.g. VREFINT channel misconfigured) too.
  EXPECT_EQ(vcu::ComputeVdda(100, 1650, 4095, P().analog, &from), 3.3f);
  EXPECT_FALSE(from);
}

TEST(Analog, DividerMathMatchesTheSchematic) {
  vcu::host::AdcScan adc1(vcu::kAdc1Channels);
  vcu::host::AdcScan adc2(vcu::kAdc2Channels);
  adc1.Set(vcu::kAdc1Vrefint, 1500);
  // 4.5 V sensor -> 2.980 V pin through 5.1k/10k.
  adc1.SetVolts(vcu::kAdc1Apps1, 4.5f * 10.0f / 15.1f, 3.3f);
  // 24 V loop -> 2.89 V pin through 100k/13.7k.
  adc2.SetVolts(vcu::kAdc2ShutdownIn, 24.0f * 13.7f / 113.7f, 3.3f);
  const vcu::AnalogReadings r = vcu::ReadAnalog(&adc1, &adc2, 1650, P().analog);
  EXPECT_NEAR(r.apps1.pin_v, 2.980f, 0.002f);
  EXPECT_NEAR(r.apps1.source_v, 4.5f, 0.005f);
  EXPECT_NEAR(r.shutdown_in.pin_v, 2.892f, 0.002f);
  EXPECT_NEAR(r.shutdown_in.source_v, 24.0f, 0.03f);
}

TEST(Analog, NullBanksReadZero) {
  const vcu::AnalogReadings r =
      vcu::ReadAnalog(nullptr, nullptr, 1650, P().analog);
  EXPECT_EQ(r.apps1.counts, 0);
  EXPECT_EQ(r.vdda, 3.3f);
}

// Lookup
TEST(Lookup, InterpolatesAndClamps) {
  const float y[] = {0.0f, 10.0f, 20.0f};
  EXPECT_FLOAT_EQ(vcu::Lookup1D(y, 3, 0.0f, 2.0f, -1.0f), 0.0f);
  EXPECT_FLOAT_EQ(vcu::Lookup1D(y, 3, 0.0f, 2.0f, 0.5f), 5.0f);
  EXPECT_FLOAT_EQ(vcu::Lookup1D(y, 3, 0.0f, 2.0f, 1.5f), 15.0f);
  EXPECT_FLOAT_EQ(vcu::Lookup1D(y, 3, 0.0f, 2.0f, 9.0f), 20.0f);
}

// Pedals
vcu::PedalInputs Pedal(float travel, float brake_psi = 0.0f) {
  const auto& a = P().apps;
  const auto& b = P().bse;
  vcu::PedalInputs in;
  in.apps1_v = a.apps1_v_at_0 + travel * (a.apps1_v_at_100 - a.apps1_v_at_0);
  in.apps2_v = a.apps2_v_at_0 + travel * (a.apps2_v_at_100 - a.apps2_v_at_0);
  const float bv = b.v_at_0_psi +
                   brake_psi / b.full_scale_psi * (b.v_at_full - b.v_at_0_psi);
  in.bse1_v = bv;
  in.bse2_v = bv;
  in.bse1_pin_v = bv * P().analog.sensor_divider_ratio;
  in.bse2_pin_v = in.bse1_pin_v;
  return in;
}

vcu::PedalOutputs Settle(vcu::PedalProcessor& pp, const vcu::PedalInputs& in,
                         uint32_t* t, uint32_t ms) {
  vcu::PedalOutputs out;
  for (uint32_t end = *t + ms; *t < end; *t += 3) {
    out = pp.Update(in, *t, P().apps, P().bse);
  }
  return out;
}

TEST(Pedals, TravelThroughFilterAndDeadzone) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  EXPECT_EQ(Settle(pp, Pedal(0.05f), &t, 300).pedal_travel, 0.0f);
  EXPECT_EQ(Settle(pp, Pedal(0.9f), &t, 300).pedal_travel, 1.0f);
  const float mid = Settle(pp, Pedal(0.415f), &t, 300).pedal_travel;
  EXPECT_NEAR(mid, (0.415f - 0.09f) / (0.74f - 0.09f), 0.01f);
}

TEST(Pedals, DeviationOver10PercentFor100msIsImplausible) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  vcu::PedalInputs in = Pedal(0.5f);
  Settle(pp, in, &t, 30);
  const auto& a = P().apps;
  in.apps2_v = a.apps2_v_at_0 + 0.38f * (a.apps2_v_at_100 - a.apps2_v_at_0);
  vcu::PedalOutputs out = Settle(pp, in, &t, 90);
  EXPECT_TRUE(out.apps_mismatch);
  EXPECT_FALSE(out.apps_implausible);
  out = Settle(pp, in, &t, 15);
  EXPECT_TRUE(out.apps_implausible);
  // Clears as soon as the deviation is back inside the limit.
  out = Settle(pp, Pedal(0.5f), &t, 3);
  EXPECT_FALSE(out.apps_implausible);
  // 9% deviation never trips.
  in.apps2_v = a.apps2_v_at_0 + 0.41f * (a.apps2_v_at_100 - a.apps2_v_at_0);
  EXPECT_FALSE(Settle(pp, in, &t, 300).apps_implausible);
}

TEST(Pedals, OpenAndShortWindows) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  vcu::PedalInputs in = Pedal(0.0f);
  in.apps1_v = 0.1f;  // open: pulled to ground by the 10k
  in.bse2_v = 4.9f;   // short to the 5 V rail
  vcu::PedalOutputs out = Settle(pp, in, &t, 30);
  EXPECT_TRUE(out.apps1_disconnect);
  EXPECT_TRUE(out.bse2_out_of_range);
  EXPECT_FALSE(out.bse2_fault);  // range faults persist 100 ms first
  out = Settle(pp, in, &t, 120);
  EXPECT_TRUE(out.apps_implausible);
  EXPECT_TRUE(out.bse2_fault);
  EXPECT_FALSE(out.bse1_fault);
}

TEST(Pedals, BrakeOverThrottleLatchesUntilBelow5Percent) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  EXPECT_FALSE(Settle(pp, Pedal(0.6f), &t, 300).brake_over_throttle);
  EXPECT_TRUE(Settle(pp, Pedal(0.6f, 600.0f), &t, 300).brake_over_throttle);
  EXPECT_TRUE(Settle(pp, Pedal(0.6f), &t, 300).brake_over_throttle);
  EXPECT_TRUE(Settle(pp, Pedal(0.13f), &t, 300).brake_over_throttle);
  EXPECT_FALSE(Settle(pp, Pedal(0.0f), &t, 300).brake_over_throttle);
}

TEST(Pedals, BrakePressedHysteresisAndLight) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  vcu::PedalOutputs out = Settle(pp, Pedal(0.0f, 100.0f), &t, 300);
  EXPECT_FALSE(out.brake_pressed);
  EXPECT_TRUE(out.brake_for_rtd);
  EXPECT_FLOAT_EQ(out.brake_light, 0.30f);
  EXPECT_TRUE(Settle(pp, Pedal(0.0f, 320.0f), &t, 300).brake_pressed);
  EXPECT_TRUE(Settle(pp, Pedal(0.0f, 270.0f), &t, 300).brake_pressed);
  EXPECT_FALSE(Settle(pp, Pedal(0.0f, 240.0f), &t, 300).brake_pressed);
  out = Settle(pp, Pedal(0.0f, 0.0f), &t, 300);
  EXPECT_EQ(out.brake_light, 0.0f);
}

TEST(Pedals, HardBrakeCrossCheck) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  vcu::PedalInputs in = Pedal(0.0f);
  in.bse1_pin_v = in.bse2_pin_v = 0.9f;  // above the 0.8 V default
  in.hard_braking_hw = true;
  vcu::PedalOutputs out = Settle(pp, in, &t, 200);
  EXPECT_TRUE(out.firmware_hard_brake);
  EXPECT_FALSE(out.hard_brake_mismatch);
  in.hard_braking_hw = false;
  EXPECT_FALSE(Settle(pp, in, &t, 30).hard_brake_mismatch);
  EXPECT_TRUE(Settle(pp, in, &t, 30).hard_brake_mismatch);
}

// Steering
TEST(Steering, PortedMappingAndRange) {
  // pct 0.33 is the 2026 car's centre (+7 deg offset).
  const float v = 0.5f + 0.33f * 4.0f;
  EXPECT_NEAR(vcu::ComputeSteering(v, P().steering).angle_deg,
              ((0.33f - 0.02f) / 0.62f - 0.5f) * 230.0f + 7.0f, 0.01f);
  EXPECT_FALSE(vcu::ComputeSteering(v, P().steering).range_fault);
  EXPECT_TRUE(vcu::ComputeSteering(0.1f, P().steering).range_fault);
  EXPECT_TRUE(vcu::ComputeSteering(4.9f, P().steering).range_fault);
}

TEST(BrakeBias, HoldsBelowThreshold) {
  vcu::BrakeBias bias;
  EXPECT_EQ(bias.Update(400, 400, 1000), 0.0f);
  EXPECT_FLOAT_EQ(bias.Update(900, 300, 1000), 0.75f);
  EXPECT_FLOAT_EQ(bias.Update(100, 100, 1000), 0.75f);
}

// Shutdown loop
ShutdownLoopState Loop(float in_loop_v, float out_loop_v) {
  vcu::ShutdownLoop loop;
  const float r = P().analog.shutdown_divider_ratio;
  ShutdownLoopState s = ShutdownLoopState::kUnknown;
  for (uint32_t t = 0; t <= 30; t += 3) {
    s = loop.Update(in_loop_v * r, out_loop_v * r, t, P().shutdown);
  }
  return s;
}

TEST(ShutdownLoop, TruthTable) {
  EXPECT_EQ(Loop(24, 24), ShutdownLoopState::kClosed);
  EXPECT_EQ(Loop(24, 0), ShutdownLoopState::kBspdRelayOpen);
  EXPECT_EQ(Loop(0, 0), ShutdownLoopState::kUpstreamOpen);
  EXPECT_EQ(Loop(0, 24), ShutdownLoopState::kImplausible);
  // 19.2 V (2.31 V at the pin) is the "high" threshold.
  EXPECT_EQ(Loop(19.0f, 19.0f), ShutdownLoopState::kUpstreamOpen);
  EXPECT_EQ(Loop(19.5f, 19.5f), ShutdownLoopState::kClosed);
}

TEST(ShutdownLoop, UnknownUntilDebounced) {
  vcu::ShutdownLoop loop;
  const float v = 24.0f * P().analog.shutdown_divider_ratio;
  EXPECT_EQ(loop.Update(v, v, 0, P().shutdown), ShutdownLoopState::kUnknown);
  EXPECT_FALSE(loop.closed());
}

TEST(ShutdownLoop, HysteresisHoldsNearThreshold) {
  vcu::ShutdownLoop loop;
  const float r = P().analog.shutdown_divider_ratio;
  uint32_t t = 0;
  for (; t <= 30; t += 3) loop.Update(24 * r, 24 * r, t, P().shutdown);
  // 18 V is between the low (16.6 V) and high (19.2 V) thresholds.
  for (; t <= 90; t += 3) loop.Update(18 * r, 18 * r, t, P().shutdown);
  EXPECT_TRUE(loop.closed());
  for (; t <= 150; t += 3) loop.Update(15 * r, 15 * r, t, P().shutdown);
  EXPECT_FALSE(loop.closed());
}

// Faults
TEST(Faults, PrimaryFaultPriority) {
  using lhre::can::VcuFaultCode;
  EXPECT_EQ(vcu::PrimaryFault(0), VcuFaultCode::kNone);
  EXPECT_EQ(vcu::PrimaryFault(vcu::kFaultDuiTimeout),
            VcuFaultCode::kDuiTimeout);
  EXPECT_EQ(vcu::PrimaryFault(vcu::kFaultDuiTimeout | vcu::kFaultBspdTripped |
                              vcu::kFaultAppsImplausible),
            VcuFaultCode::kBspdTripped);
  EXPECT_EQ(
      vcu::PrimaryFault(vcu::kFaultAppsImplausible | vcu::kFaultHvcTimeout),
      VcuFaultCode::kHvcTimeout);
}

TEST(Faults, BrakeFlagLatch) {
  vcu::BrakeFlagLatch latch;
  latch.Update(false, true);
  EXPECT_FALSE(latch.latched());
  latch.Update(true, false);
  EXPECT_TRUE(latch.latched());
  EXPECT_TRUE(latch.dirty());
  EXPECT_EQ(latch.persisted_value(), vcu::BrakeFlagLatch::kPersistedSet);
  latch.MarkPersisted();
  latch.Update(true, true);  // reset refused while the flag is active
  EXPECT_TRUE(latch.latched());
  latch.Update(false, false);
  EXPECT_TRUE(latch.latched());  // no auto-clear
  latch.Update(false, true);
  EXPECT_FALSE(latch.latched());
  EXPECT_EQ(latch.resets(), 1);

  vcu::BrakeFlagLatch restored;
  restored.Restore(vcu::BrakeFlagLatch::kPersistedSet);
  EXPECT_TRUE(restored.latched());
  restored.Restore(0x12345678u);  // corrupt/erased storage reads as clear
  EXPECT_FALSE(restored.latched());
}

TEST(Faults, ErrorLedsDefaultOnAndLatch) {
  vcu::ErrorLeds leds;
  EXPECT_TRUE(leds.bms_on());
  leds.Update(true, false, false);
  EXPECT_FALSE(leds.bms_on());
  leds.Update(true, true, false);
  leds.Update(true, false, false);
  EXPECT_TRUE(leds.bms_on());
  EXPECT_FALSE(leds.imd_on());
  leds.Update(false, false, false);
  EXPECT_TRUE(leds.imd_on());
}

// Torque
TEST(Torque, MapPowerLimitAndDerate) {
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 1.0f;
  in.motor_rpm = 0.0f;
  in.dt_ms = 3;
  in.ocv_min_cell_v = 3.0f;  // halfway into the 2.8-3.2 V derate
  vcu::TorqueOutputs out =
      path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_FLOAT_EQ(out.lookup, 210.0f);
  EXPECT_NEAR(out.derated, 105.0f, 0.01f);
  // Derate not applied (ported behavior), no power: no trim.
  EXPECT_FLOAT_EQ(out.power_limited, 210.0f);

  // Half pedal: 0.5^1.5 of the 6000 rpm row.
  in.pedal_travel = 0.5f;
  in.motor_rpm = 6000.0f;
  out = path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_NEAR(out.lookup, 103.0f * 0.35355f, 0.01f);

  // 80 kW against a 70 kW limit trims torque.
  in.pedal_travel = 1.0f;
  in.dc_bus_voltage = 500.0f;
  in.dc_bus_current = 160.0f;
  out = path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_LT(out.power_limited, out.lookup);
  EXPECT_GE(out.power_limited, 0.0f);
}

TEST(Torque, SkidpadParamsSaturateAt70Percent) {
  const vcu::Params& skid = vcu::ParamsFor(lhre::can::EventMode::kSkidpad);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 1.0f;
  in.dt_ms = 3;
  const vcu::TorqueOutputs out =
      path.Update(in, skid.torque, skid.power_limit, skid.traction);
  EXPECT_NEAR(out.lookup, 210.0f * 0.70f, 0.01f);
}

// Battery
TEST(Battery, SoeTracksRestedCellVoltage) {
  vcu::BatteryEstimator est;
  vcu::BatteryInputs in;
  in.min_cell_v = 3.78f;  // 46% on the table
  in.max_cell_v = 3.80f;
  in.dt_ms = 3;
  vcu::BatteryOutputs out;
  for (int i = 0; i < 300; ++i) out = est.Update(in, P().battery);
  EXPECT_EQ(out.soe_pct, 0.0f);  // < 1 s rest
  for (int i = 0; i < 3000; ++i) out = est.Update(in, P().battery);
  EXPECT_NEAR(out.soe_pct, 46.0f, 1.0f);
  // Under load the estimate holds.
  in.dc_bus_current = 100.0f;
  in.min_cell_v = 3.5f;
  for (int i = 0; i < 1000; ++i) out = est.Update(in, P().battery);
  EXPECT_NEAR(out.soe_pct, 46.0f, 1.0f);
}

TEST(Battery, EnergyMetering) {
  vcu::BatteryEstimator est;
  vcu::BatteryInputs in;
  in.dc_bus_voltage = 500.0f;
  in.dc_bus_current = 100.0f;  // 50 kW
  in.dt_ms = 1000;
  vcu::BatteryOutputs out = est.Update(in, P().battery);
  EXPECT_NEAR(out.net_energy_wh, 50000.0 / 3600.0, 1e-6);
  in.dc_bus_current = -20.0f;  // regen 10 kW
  out = est.Update(in, P().battery);
  EXPECT_NEAR(out.regen_energy_wh, 10000.0 / 3600.0, 1e-6);
}

// Buzzer
TEST(Buzzer, DurationCapAndLoopCut) {
  vcu::Buzzer bz;
  bz.Start(0, 1200);
  EXPECT_TRUE(bz.Update(1199, true, 1500));
  EXPECT_FALSE(bz.Update(1200, true, 1500));
  bz.Start(0, 5000);  // longer than the local ceiling
  EXPECT_TRUE(bz.Update(1499, true, 1500));
  EXPECT_FALSE(bz.Update(1500, true, 1500));
  bz.Start(0, 1200);
  EXPECT_FALSE(bz.Update(10, false, 1500));  // loop open
}

TEST(Buzzer, SineTable) {
  uint16_t lut[32];
  vcu::BuildSineTable(lut, 32, 1.0f);
  EXPECT_EQ(lut[0], vcu::kDacMidScale);
  EXPECT_EQ(lut[8], 4095);
  EXPECT_EQ(lut[24], 1);
  vcu::BuildSineTable(lut, 32, 0.0f);
  EXPECT_EQ(lut[8], vcu::kDacMidScale);
}

}  // namespace
