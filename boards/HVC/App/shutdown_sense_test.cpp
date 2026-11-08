// Host tests for shutdown-chain diagnosis.

#include "shutdown_sense.hpp"

#include <gtest/gtest.h>

namespace {

hvc::Legs AllLive() {
  hvc::Legs legs;
  legs.leg2 = legs.leg3 = legs.leg4 = legs.leg5 = legs.leg15 = true;
  return legs;
}

TEST(ShutdownSense, CompleteChain) {
  EXPECT_EQ(hvc::Diagnose(AllLive()), hvc::ChainBreak::kNone);
  EXPECT_FALSE(hvc::Inconsistent(AllLive()));
}

TEST(ShutdownSense, FindsFirstDeadLeg) {
  hvc::Legs legs = AllLive();
  legs.leg15 = false;
  EXPECT_EQ(hvc::Diagnose(legs), hvc::ChainBreak::kCar);
  legs.leg5 = false;
  EXPECT_EQ(hvc::Diagnose(legs), hvc::ChainBreak::kInterlock);
  legs.leg4 = false;
  EXPECT_EQ(hvc::Diagnose(legs), hvc::ChainBreak::kImdRelay);
  legs.leg3 = false;
  EXPECT_EQ(hvc::Diagnose(legs), hvc::ChainBreak::kBmsRelay);
  legs.leg2 = false;
  EXPECT_EQ(hvc::Diagnose(legs), hvc::ChainBreak::kNoInput);
  EXPECT_FALSE(hvc::Inconsistent(legs));
}

TEST(ShutdownSense, FlagsLiveLegDownstreamOfDeadOne) {
  hvc::Legs legs = AllLive();
  legs.leg3 = false;  // BMS relay open, yet leg 4 still reads live
  EXPECT_EQ(hvc::Diagnose(legs), hvc::ChainBreak::kBmsRelay);
  EXPECT_TRUE(hvc::Inconsistent(legs));
}

}  // namespace
