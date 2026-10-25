#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/pedals.hpp"

namespace {

const vcu::Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

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
  EXPECT_TRUE(Settle(pp, Pedal(0.06f), &t, 300).brake_over_throttle);
  EXPECT_FALSE(Settle(pp, Pedal(0.04f), &t, 300).brake_over_throttle);
}

// The thresholds are raw pedal travel, not the deadzoned torque input.
TEST(Pedals, BrakeOverThrottleTripsAt25PercentRawTravel) {
  vcu::PedalProcessor pp;
  uint32_t t = 0;
  EXPECT_FALSE(Settle(pp, Pedal(0.24f, 600.0f), &t, 300).brake_over_throttle);
  EXPECT_TRUE(Settle(pp, Pedal(0.26f, 600.0f), &t, 300).brake_over_throttle);
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

}  // namespace
