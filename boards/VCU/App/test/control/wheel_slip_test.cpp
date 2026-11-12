#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/wheel_slip.hpp"

namespace {

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

TEST(WheelSlip, NoSlipThrottleStepsAreNotRecoveryLimited) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  p.enabled = true;
  vcu::WheelInputs in;
  in.fresh = true;
  for (float& speed : in.rad_s) speed = 50;
  EXPECT_FLOAT_EQ(control.Update(in, 0, 3, p).positive_nm, 0);
  EXPECT_FLOAT_EQ(control.Update(in, 200, 3, p).positive_nm, 200);
  EXPECT_FLOAT_EQ(control.Update(in, 250, 3, p).positive_nm, 250);
  EXPECT_FLOAT_EQ(control.Update(in, 0, 3, p).positive_nm, 0);
  EXPECT_FLOAT_EQ(control.Update(in, 200, 3, p).positive_nm, 200);
}

TEST(WheelSlip, FirstSlipCutUsesPiTargetBeforeRecoveryRamping) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  p.enabled = true;
  p.ki_nm_s = 0;
  vcu::WheelInputs in;
  in.fresh = true;
  for (float& speed : in.rad_s) speed = 50;
  in.rad_s[2] = 60;  // 20% slip, 10 points above target: a 10 Nm cut
  EXPECT_NEAR(control.Update(in, 200, 3, p).positive_nm, 190, 1e-3f);
  in.rad_s[2] = 50;
  EXPECT_NEAR(control.Update(in, 200, 3, p).positive_nm, 191.2f, 1e-3f);
}

TEST(WheelSlip, OnlyPostCutRecoveryIsRampedAndPedalReleaseClearsIt) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  p.enabled = true;
  p.ki_nm_s = 0;  // isolate the recovery cap from integral decay
  vcu::WheelInputs in;
  in.fresh = true;
  for (float& speed : in.rad_s) speed = 50;
  ASSERT_FLOAT_EQ(control.Update(in, 200, 3, p).positive_nm, 200);
  in.rad_s[2] = 200;
  ASSERT_FLOAT_EQ(control.Update(in, 200, 3, p).positive_nm, 0);
  in.rad_s[2] = 50;
  const float step = p.recovery_nm_s * 0.003f;
  EXPECT_NEAR(control.Update(in, 200, 3, p).positive_nm, step, 1e-5f);
  float previous = step;
  for (int n = 0; n < 200; ++n) {
    const float next = control.Update(in, 200, 3, p).positive_nm;
    EXPECT_LE(next - previous, step + 1e-4f);
    previous = next;
  }
  EXPECT_FLOAT_EQ(previous, 200);
  EXPECT_FLOAT_EQ(control.Update(in, 250, 3, p).positive_nm, 250);
  in.rad_s[2] = 200;
  control.Update(in, 200, 3, p);
  control.Update(in, 0, 3, p);
  in.rad_s[2] = 50;
  EXPECT_FLOAT_EQ(control.Update(in, 200, 3, p).positive_nm, 200);
  in.fresh = false;
  EXPECT_EQ(control.Update(in, 200, 3, p).positive_nm, 0);
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

}  // namespace
