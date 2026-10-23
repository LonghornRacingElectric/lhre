// Host unit tests for the traction control law. Expected trims are worked
// out from #298's tuning: threshold 30 rad/s², kp 0.5, ki 5.0, max 50 N·m.

#include "traction_control.hpp"

#include <gtest/gtest.h>

namespace {

using vcu::TractionControl;

constexpr float kDtS = 0.003f;
constexpr float kToleranceNm = 1e-3f;

// Seeds the speed at 0, then holds the shaft acceleration for `steps`.
float ApplyAfterSteadyAcceleration(TractionControl& tc, float upstream_nm,
                                   float alpha_rad_s2, int steps,
                                   float dt_s = kDtS) {
  tc.Apply(upstream_nm, 0.0f, dt_s);
  float speed_rad_s = 0.0f;
  float torque_nm = upstream_nm;
  for (int step = 0; step < steps; ++step) {
    speed_rad_s += alpha_rad_s2 * dt_s;
    torque_nm = tc.Apply(upstream_nm, speed_rad_s, dt_s);
  }
  return torque_nm;
}

TEST(TractionControl, FirstSampleOnlySeedsTheMotorSpeed) {
  TractionControl tc;
  EXPECT_FLOAT_EQ(tc.Apply(100.0f, 160.0f, kDtS), 100.0f);
}

TEST(TractionControl, PassesTorqueThroughBelowTheThreshold) {
  TractionControl tc;
  EXPECT_NEAR(ApplyAfterSteadyAcceleration(tc, 100.0f, 20.0f, 1), 100.0f,
              kToleranceNm);
}

TEST(TractionControl, TrimsByPiOnTheExcessAcceleration) {
  TractionControl tc;
  // Error 10 rad/s²: 0.5·10 + 5.0·(10·0.003) = 5.15 N·m.
  EXPECT_NEAR(ApplyAfterSteadyAcceleration(tc, 100.0f, 40.0f, 1), 94.85f,
              kToleranceNm);
}

TEST(TractionControl, IntegralGrowsWhileAboveTheThreshold) {
  TractionControl tc;
  // Second step: 0.5·10 + 5.0·(2·10·0.003) = 5.3 N·m.
  EXPECT_NEAR(ApplyAfterSteadyAcceleration(tc, 100.0f, 40.0f, 2), 94.7f,
              kToleranceNm);
}

TEST(TractionControl, IntegralResetsWhenAccelerationDropsBelowTheThreshold) {
  TractionControl tc;
  ApplyAfterSteadyAcceleration(tc, 100.0f, 40.0f, 2);
  const float held_speed_rad_s = 2 * 40.0f * kDtS;
  EXPECT_NEAR(tc.Apply(100.0f, held_speed_rad_s, kDtS), 100.0f, kToleranceNm);
  // Back above: the trim starts again from one step of integral.
  EXPECT_NEAR(tc.Apply(100.0f, held_speed_rad_s + 40.0f * kDtS, kDtS), 94.85f,
              kToleranceNm);
}

TEST(TractionControl, TrimIsLimitedToMaxTrim) {
  TractionControl tc;
  EXPECT_NEAR(ApplyAfterSteadyAcceleration(tc, 100.0f, 1000.0f, 1), 50.0f,
              kToleranceNm);
}

// TC-01: traction control only removes torque, so it never goes below 0.
TEST(TractionControl, TrimNeverExceedsTheUpstreamTorque) {
  TractionControl tc;
  EXPECT_NEAR(ApplyAfterSteadyAcceleration(tc, 10.0f, 1000.0f, 1), 0.0f,
              kToleranceNm);
}

// #298 divides by a hard-coded 3 ms. Over a 10 ms step that reads 40 rad/s²
// as 133 rad/s² and saturates the trim.
TEST(TractionControl, DifferentiatesOverTheMeasuredStep) {
  TractionControl tc;
  // 0.5·10 + 5.0·(10·0.010) = 5.5 N·m.
  EXPECT_NEAR(ApplyAfterSteadyAcceleration(tc, 100.0f, 40.0f, 1, 0.010f),
              94.5f, kToleranceNm);
}

}  // namespace
