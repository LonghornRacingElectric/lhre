#include <gtest/gtest.h>

#include "config/params.hpp"
#include "safety/shutdown_loop.hpp"

namespace {

using lhre::can::ShutdownLoopState;

const vcu::Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

ShutdownLoopState Loop(float in_loop_v, float out_loop_v) {
  vcu::ShutdownLoop loop;
  const float r = P().analog.shutdown_divider_ratio;
  ShutdownLoopState s = ShutdownLoopState::kUnknown;
  for (uint32_t t = 0; t <= 30; t += 3) {
    s = loop.Update(in_loop_v * r, out_loop_v * r, t, P().shutdown);
  }
  return s;
}

TEST(ShutdownLoop, TruthTable) {
  EXPECT_EQ(Loop(24, 24), ShutdownLoopState::kClosed);
  EXPECT_EQ(Loop(24, 0), ShutdownLoopState::kBspdRelayOpen);
  EXPECT_EQ(Loop(0, 0), ShutdownLoopState::kUpstreamOpen);
  EXPECT_EQ(Loop(0, 24), ShutdownLoopState::kImplausible);
  // 19.2 V (2.31 V at the pin) is the "high" threshold.
  EXPECT_EQ(Loop(19.0f, 19.0f), ShutdownLoopState::kUpstreamOpen);
  EXPECT_EQ(Loop(19.5f, 19.5f), ShutdownLoopState::kClosed);
}

TEST(ShutdownLoop, UnknownUntilDebounced) {
  vcu::ShutdownLoop loop;
  const float v = 24.0f * P().analog.shutdown_divider_ratio;
  EXPECT_EQ(loop.Update(v, v, 0, P().shutdown), ShutdownLoopState::kUnknown);
  EXPECT_FALSE(loop.closed());
}

TEST(ShutdownLoop, HysteresisHoldsNearThreshold) {
  vcu::ShutdownLoop loop;
  const float r = P().analog.shutdown_divider_ratio;
  uint32_t t = 0;
  for (; t <= 30; t += 3) loop.Update(24 * r, 24 * r, t, P().shutdown);
  // 18 V is between the low (16.6 V) and high (19.2 V) thresholds.
  for (; t <= 90; t += 3) loop.Update(18 * r, 18 * r, t, P().shutdown);
  EXPECT_TRUE(loop.closed());
  for (; t <= 150; t += 3) loop.Update(15 * r, 15 * r, t, P().shutdown);
  EXPECT_FALSE(loop.closed());
}

}  // namespace
