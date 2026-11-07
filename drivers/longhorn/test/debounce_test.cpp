#include <gtest/gtest.h>

#include <cstdint>

#include "longhorn/debounce.hpp"

namespace {

TEST(Debouncer, NeedsStableLevelForTheWholeWindow) {
  longhorn::Debouncer d;
  EXPECT_FALSE(d.Update(true, 0, 10));
  EXPECT_FALSE(d.Update(true, 9, 10));
  EXPECT_TRUE(d.Update(true, 10, 10));
  EXPECT_TRUE(d.Update(false, 11, 10));  // glitch
  EXPECT_TRUE(d.Update(true, 12, 10));
  EXPECT_TRUE(d.Update(false, 13, 10));
  EXPECT_FALSE(d.Update(false, 23, 10));
}

TEST(EdgeButton, HeldAtBootNeedsAReleaseFirst) {
  longhorn::EdgeButton b;
  uint32_t t = 0;
  for (; t < 100; ++t) {
    EXPECT_FALSE(b.Update(true, t, 20));
  }
  for (; t < 130; ++t) {
    EXPECT_FALSE(b.Update(false, t, 20));
  }
  bool edge = false;
  for (; t < 160; ++t) {
    edge = edge || b.Update(true, t, 20);
  }
  EXPECT_TRUE(edge);
  // One edge per press.
  for (; t < 300; ++t) {
    EXPECT_FALSE(b.Update(true, t, 20));
  }
}

TEST(PersistenceTimer, HoldsAfterDurationAndDropsAtOnce) {
  longhorn::PersistenceTimer p;
  EXPECT_FALSE(p.Update(true, 0, 100));
  EXPECT_FALSE(p.Update(true, 99, 100));
  EXPECT_TRUE(p.Update(true, 100, 100));
  EXPECT_FALSE(p.Update(false, 101, 100));
  EXPECT_FALSE(p.Update(true, 102, 100));  // restarts
}

}  // namespace
