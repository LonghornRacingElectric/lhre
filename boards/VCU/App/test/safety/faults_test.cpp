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
  latch.Update(false, true);
  EXPECT_FALSE(latch.latched());
  latch.Update(true, false);
  EXPECT_TRUE(latch.latched());
  EXPECT_TRUE(latch.dirty());
  EXPECT_EQ(latch.persisted_value(), vcu::BrakeFlagLatch::kPersistedSet);
  latch.MarkPersisted();
  latch.Update(true, true);  // reset refused while the flag is active
  EXPECT_TRUE(latch.latched());
  latch.Update(false, false);
  EXPECT_TRUE(latch.latched());  // no auto-clear
  latch.Update(false, true);
  EXPECT_FALSE(latch.latched());
  EXPECT_EQ(latch.resets(), 1);

  vcu::BrakeFlagLatch restored;
  restored.Restore(vcu::BrakeFlagLatch::kPersistedSet);
  EXPECT_TRUE(restored.latched());
  restored.Restore(0x12345678u);  // corrupt/erased storage reads as clear
  EXPECT_FALSE(restored.latched());
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
