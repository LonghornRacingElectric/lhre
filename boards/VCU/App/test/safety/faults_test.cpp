#include <gtest/gtest.h>

#include "safety/faults.hpp"

namespace {

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
  latch.Update(false);
  EXPECT_FALSE(latch.latched());
  latch.Update(true);
  EXPECT_TRUE(latch.latched());
  latch.Update(false);
  EXPECT_TRUE(latch.latched());  // no auto-clear, no reset
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

}  // namespace
