#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/steering.hpp"

namespace {

const vcu::Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

TEST(Steering, PortedMappingAndRange) {
  // pct 0.33 is the 2026 car's centre (+7 deg offset).
  const float v = 0.5f + 0.33f * 4.0f;
  EXPECT_NEAR(vcu::ComputeSteering(v, P().steering).angle_deg,
              ((0.33f - 0.02f) / 0.62f - 0.5f) * 230.0f + 7.0f, 0.01f);
  EXPECT_FALSE(vcu::ComputeSteering(v, P().steering).range_fault);
  EXPECT_TRUE(vcu::ComputeSteering(0.1f, P().steering).range_fault);
  EXPECT_TRUE(vcu::ComputeSteering(4.9f, P().steering).range_fault);
}

}  // namespace
