// Tests for RainbowTask's Step(), the LED writer behind the status rainbow
// and its error-color override. Scheduler-less: Step() is driven directly.

#include "longhorn/rainbow_task.hpp"

#include <gtest/gtest.h>

#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"

namespace {

using longhorn::RainbowTask;

class RainbowTaskTest : public ::testing::Test {
 protected:
  lhal::host::Pwm red_;
  lhal::host::Pwm green_;
  lhal::host::Pwm blue_;
  longhorn::RgbLed led_{&red_, &green_, &blue_};
  lhal::host::TestClock clock_;
  RainbowTask task_{&led_, &clock_};
};

TEST_F(RainbowTaskTest, FirstStepOnlyStartsTheClock) {
  task_.Override(1.0f, 0.0f, 0.0f);
  task_.Step(5000);
  EXPECT_FLOAT_EQ(red_.duty(), 0.5f);  // still the boot white
  EXPECT_FLOAT_EQ(green_.duty(), 0.5f);
}

TEST_F(RainbowTaskTest, WritesNothingUntilAFullPeriodHasPassed) {
  task_.Override(1.0f, 0.0f, 0.0f);
  task_.Step(0);
  task_.Step(RainbowTask::kPeriodMs - 1);
  EXPECT_FLOAT_EQ(red_.duty(), 0.5f);
  task_.Step(RainbowTask::kPeriodMs);
  EXPECT_FLOAT_EQ(red_.duty(), 1.0f);
}

TEST_F(RainbowTaskTest, OverrideShowsItsColorAtEightBitsPerChannel) {
  task_.Override(1.0f, 0.0f, 0.5f);
  task_.Step(0);
  task_.Step(RainbowTask::kPeriodMs);
  EXPECT_FLOAT_EQ(red_.duty(), 1.0f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(blue_.duty(), 128.0f / 255.0f);
}

TEST_F(RainbowTaskTest, OverrideClampsOutOfRangeChannels) {
  task_.Override(-0.5f, 2.0f, 0.0f);
  task_.Step(0);
  task_.Step(RainbowTask::kPeriodMs);
  EXPECT_FLOAT_EQ(red_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(green_.duty(), 1.0f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);
}

// Black is a real override color, not "no override".
TEST_F(RainbowTaskTest, BlackOverrideTurnsTheLedOff) {
  task_.Override(0.0f, 0.0f, 0.0f);
  task_.Step(0);
  task_.Step(RainbowTask::kPeriodMs);
  EXPECT_FLOAT_EQ(red_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);
}

TEST_F(RainbowTaskTest, AutoGoesBackToTheRainbow) {
  task_.Override(0.0f, 0.0f, 1.0f);
  task_.Step(0);
  task_.Step(RainbowTask::kPeriodMs);
  ASSERT_FLOAT_EQ(blue_.duty(), 1.0f);

  task_.Auto();
  task_.Step(2 * RainbowTask::kPeriodMs);
  // Early in the cycle the rainbow is mostly red, at most half brightness.
  EXPECT_GT(red_.duty(), 0.4f);
  EXPECT_LE(red_.duty(), 0.5f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);
}

// A late tick advances the animation by the real time that passed, so a
// busy system doesn't slow the rainbow down.
TEST_F(RainbowTaskTest, LateStepAdvancesByTheTimeThatPassed) {
  lhal::host::Pwm r2;
  lhal::host::Pwm g2;
  lhal::host::Pwm b2;
  longhorn::RgbLed led2{&r2, &g2, &b2};
  RainbowTask on_time{&led2, &clock_};

  task_.Step(0);
  task_.Step(66);  // one late tick
  on_time.Step(0);
  on_time.Step(33);
  on_time.Step(66);
  EXPECT_NEAR(red_.duty(), r2.duty(), 1e-5);
  EXPECT_NEAR(green_.duty(), g2.duty(), 1e-5);
  EXPECT_NEAR(blue_.duty(), b2.duty(), 1e-5);
}

TEST(RainbowTask, WithoutAnLedEverythingIsANoop) {
  lhal::host::TestClock clock;
  RainbowTask task{nullptr, &clock};
  task.Start();  // creates no task
  task.Override(1.0f, 1.0f, 1.0f);
  task.Step(0);
  task.Step(1000);
}

}  // namespace
