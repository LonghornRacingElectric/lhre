#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/launch.hpp"

namespace {

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

}  // namespace
