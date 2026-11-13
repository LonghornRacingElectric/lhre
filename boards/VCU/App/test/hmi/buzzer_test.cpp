#include <gtest/gtest.h>

#include "config/params.hpp"
#include "hmi/buzzer.hpp"

namespace {

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
