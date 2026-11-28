#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/linelock.hpp"

namespace {

vcu::LineLockParams On() {
  vcu::LineLockParams p;
  p.enabled = true;
  return p;
}

vcu::LineLockInputs Healthy() {
  vcu::LineLockInputs in;
  in.drive = true;
  in.soc_pct = 80.0f;
  in.speed_valid = true;
  in.motor_rpm = 1000.0f;
  return in;
}

TEST(LineLock, OffByDefault) {
  vcu::LineLockPolicy ll;
  EXPECT_FALSE(vcu::LineLockParams{}.enabled);
  EXPECT_FALSE(ll.Update(Healthy(), vcu::LineLockParams{}));
}

TEST(LineLock, RequestsWhenEveryConditionHolds) {
  vcu::LineLockPolicy ll;
  EXPECT_TRUE(ll.Update(Healthy(), On()));
}

TEST(LineLock, EachConditionBlocksIt) {
  const vcu::LineLockParams p = On();
  auto blocked = [&](auto change) {
    vcu::LineLockPolicy ll;
    vcu::LineLockInputs in = Healthy();
    change(in);
    return !ll.Update(in, p);
  };
  EXPECT_TRUE(blocked([](auto& in) { in.drive = false; }));
  EXPECT_TRUE(blocked([](auto& in) { in.hard_braking = true; }));
  EXPECT_TRUE(blocked([&](auto& in) { in.soc_pct = p.min_soc_pct - 1; }));
  EXPECT_TRUE(blocked([](auto& in) { in.speed_valid = false; }));
  EXPECT_TRUE(blocked([&](auto& in) { in.motor_rpm = p.min_motor_rpm - 1; }));
}

TEST(LineLock, StartsOnlyWithTheLineClearThenHoldsThroughBraking) {
  const vcu::LineLockParams p = On();
  vcu::LineLockPolicy ll;
  vcu::LineLockInputs in = Healthy();
  in.rear_pre_psi = p.engage_max_psi + 1;  // driver already braking
  EXPECT_FALSE(ll.Update(in, p));
  in.rear_pre_psi = 0;
  EXPECT_TRUE(ll.Update(in, p));
  in.rear_pre_psi = 300;  // braking now: it stays requested
  EXPECT_TRUE(ll.Update(in, p));
  in.motor_rpm = 0;  // a condition drops: released
  EXPECT_FALSE(ll.Update(in, p));
  in.motor_rpm = 1000;  // and it must see the line clear again to restart
  EXPECT_FALSE(ll.Update(in, p));
}

}  // namespace
