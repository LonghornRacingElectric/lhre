// Tests for the longhorn RGB LED driver, run against the LHAL host backend.

#include <gtest/gtest.h>

#include "lhal/host/pwm.hpp"
#include "longhorn/led.hpp"

namespace {

class LedTest : public ::testing::Test {
 protected:
  lhal::host::Pwm red_;
  lhal::host::Pwm green_;
  lhal::host::Pwm blue_;
  longhorn::RgbLed led_{&red_, &green_, &blue_};
};

TEST_F(LedTest, BootsMidBrightnessWhite) {
  EXPECT_FLOAT_EQ(red_.duty(), 0.5f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.5f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.5f);
}

TEST_F(LedTest, SetWritesEachChannel) {
  led_.Set(0.1f, 0.2f, 0.3f);
  EXPECT_FLOAT_EQ(red_.duty(), 0.1f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.2f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.3f);
}

TEST_F(LedTest, OffZerosAllChannels) {
  led_.Off();
  EXPECT_FLOAT_EQ(red_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);
}

TEST_F(LedTest, RainbowStartsRedAndRotatesTowardGreen) {
  led_.Rainbow(0.0f);
  EXPECT_FLOAT_EQ(red_.duty(), 0.5f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);

  // A third of the cycle later the LED should be fully green.
  led_.Rainbow(longhorn::RgbLed::kRainbowCycleS / 3.0f);
  EXPECT_FLOAT_EQ(red_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.5f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);
}

TEST_F(LedTest, RainbowBrightnessIsConstantAcrossTheCycle) {
  // Step through a full cycle at the task rate; at every point exactly two
  // adjacent colors are lit and their brightness sums to the cap.
  const float dt = 0.033f;
  for (float t = 0.0f; t < longhorn::RgbLed::kRainbowCycleS; t += dt) {
    led_.Rainbow(dt);
    EXPECT_NEAR(red_.duty() + green_.duty() + blue_.duty(), 0.5f, 1e-4f);
  }
}

TEST_F(LedTest, RainbowReturnsToStartAfterFullCycle) {
  led_.Rainbow(0.0f);
  // 50 exact steps of a tenth of a second cover the 5 s cycle.
  for (int i = 0; i < 50; ++i) {
    led_.Rainbow(0.1f);
  }
  EXPECT_NEAR(red_.duty(), 0.5f, 1e-3f);
  EXPECT_NEAR(green_.duty(), 0.0f, 1e-3f);
  EXPECT_NEAR(blue_.duty(), 0.0f, 1e-3f);
}

TEST_F(LedTest, DisableFreezesRainbowButNotSet) {
  led_.Rainbow(0.5f);
  led_.Disable();
  led_.Set(1.0f, 0.0f, 0.0f);  // error color
  led_.Rainbow(0.5f);
  EXPECT_FLOAT_EQ(red_.duty(), 1.0f);
  EXPECT_FLOAT_EQ(green_.duty(), 0.0f);
  EXPECT_FLOAT_EQ(blue_.duty(), 0.0f);
}

}  // namespace
