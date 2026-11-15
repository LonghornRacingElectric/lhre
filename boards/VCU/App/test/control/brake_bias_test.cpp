#include <gtest/gtest.h>

#include "control/brake_bias.hpp"

namespace {

TEST(BrakeBias, HoldsBelowThreshold) {
  vcu::BrakeBias bias;
  EXPECT_EQ(bias.Update(400, 400, 1000), 0.0f);
  EXPECT_FLOAT_EQ(bias.Update(900, 300, 1000), 0.75f);
  EXPECT_FLOAT_EQ(bias.Update(100, 100, 1000), 0.75f);
}

}  // namespace
