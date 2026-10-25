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

TEST(WheelSlip, PedalStepWithoutSlipIsNotRateLimited) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  p.enabled = true;
  vcu::WheelInputs in;
  in.fresh = true;
  for (float& speed : in.rad_s) speed = 50;
  EXPECT_EQ(control.Update(in, 0, 3, p).positive_nm, 0);
  EXPECT_FLOAT_EQ(control.Update(in, 200, 3, p).positive_nm, 200);
}

TEST(WheelSlip, RampEndsOnceTorqueCatchesDemand) {
  vcu::WheelSlipController control;
  vcu::WheelControlParams p;
  p.enabled = true;
  vcu::WheelInputs in;
  in.fresh = true;
  for (float& speed : in.rad_s) speed = 50;
  control.Update(in, 100, 3, p);
  in.rad_s[2] = 150;
  control.Update(in, 100, 3, p);
  in.rad_s[2] = 50;
  vcu::WheelControlResult out;
  for (int i = 0; i < 1000; ++i) out = control.Update(in, 100, 3, p);
  ASSERT_FLOAT_EQ(out.positive_nm, 100);
  // Back in normal mode: a demand step passes through at once.
  EXPECT_FLOAT_EQ(control.Update(in, 150, 3, p).positive_nm, 150);
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
