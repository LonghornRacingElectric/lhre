// Tests for RateGate: fixed-rate work inside a faster loop.

#include "longhorn/rate_gate.hpp"

#include <cstdint>

#include <gtest/gtest.h>

namespace {

using longhorn::RateGate;

TEST(RateGate, FirstCallIsDueImmediately) {
  RateGate gate;
  EXPECT_TRUE(gate.Due(1234, 10));
}

TEST(RateGate, NotDueBeforeThePeriodAndDueExactlyAtIt) {
  RateGate gate;
  ASSERT_TRUE(gate.Due(0, 10));
  EXPECT_FALSE(gate.Due(9, 10));
  EXPECT_TRUE(gate.Due(10, 10));
  EXPECT_FALSE(gate.Due(10, 10));  // once per deadline, not once per call
}

// A 10 ms task in a 3 ms loop. Deadlines accumulate, so it runs 100 times a
// second. Rescheduling from "now" would drift to every 12 ms (83 Hz).
TEST(RateGate, PeriodThatIsNotWholeLoopTicksDoesNotDrift) {
  RateGate gate;
  int fired = 0;
  for (uint32_t now = 0; now < 3000; now += 3) {
    fired += gate.Due(now, 10) ? 1 : 0;
  }
  EXPECT_EQ(fired, 300);
}

TEST(RateGate, StallFiresOnceThenRestartsFromNow) {
  RateGate gate;
  ASSERT_TRUE(gate.Due(0, 10));
  EXPECT_TRUE(gate.Due(500, 10));  // 49 missed deadlines: one run, no burst
  EXPECT_FALSE(gate.Due(501, 10));
  EXPECT_FALSE(gate.Due(509, 10));
  EXPECT_TRUE(gate.Due(510, 10));
}

TEST(RateGate, KeepsTimeAcrossTheMillisecondCounterWrap) {
  RateGate gate;
  const uint32_t start = UINT32_MAX - 4;
  ASSERT_TRUE(gate.Due(start, 10));
  // The deadline is past the wrap and the clock is not: a raw compare of
  // the two counters fires here.
  EXPECT_FALSE(gate.Due(start + 1, 10));
  EXPECT_FALSE(gate.Due(start + 9, 10));  // wrapped to 4
  EXPECT_TRUE(gate.Due(start + 10, 10));
}

TEST(RateGate, ZeroPeriodIsDueEveryCall) {
  RateGate gate;
  EXPECT_TRUE(gate.Due(0, 0));
  EXPECT_TRUE(gate.Due(0, 0));
  EXPECT_TRUE(gate.Due(1, 0));
}

}  // namespace
