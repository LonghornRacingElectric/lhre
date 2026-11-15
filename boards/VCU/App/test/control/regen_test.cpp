#include <gtest/gtest.h>

#include <limits>

#include "config/params.hpp"
#include "control/regen.hpp"

namespace {

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

}  // namespace
