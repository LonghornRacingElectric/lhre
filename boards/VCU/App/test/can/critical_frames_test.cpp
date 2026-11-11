#include "can/critical_frames.hpp"

#include <gtest/gtest.h>

#include "can/field_ranges.hpp"
#include "lhre_can_vcu.hpp"

namespace {

// The generated setters cast to the raw integer without saturating, so a
// value past the field's range would wrap on the bus.
TEST(CriticalFrames, TorqueRequestSaturatesAtTheFieldRange) {
  vcu::ControlSnapshot s;
  const vcu::InverterParams p;

  s.torque_command = 5000.0f;
  EXPECT_NEAR(vcu::TorqueCommandFrame(s, p).torque_request(), vcu::kS16Nm,
              0.05f);
  s.torque_command = -5000.0f;
  EXPECT_NEAR(vcu::TorqueCommandFrame(s, p).torque_request(), -vcu::kS16Nm,
              0.05f);
  s.torque_command = -33.5f;
  EXPECT_NEAR(vcu::TorqueCommandFrame(s, p).torque_request(), -33.5f, 0.05f);
}

TEST(CriticalFrames, BrakeBiasSaturatesAtZeroAndOne) {
  vcu::ControlSnapshot s;

  s.brake_bias = 3.0f;
  EXPECT_NEAR(vcu::BrakesFrame(s).brake_bias(), 1.0f, 0.005f);
  s.brake_bias = -0.5f;
  EXPECT_NEAR(vcu::BrakesFrame(s).brake_bias(), 0.0f, 0.005f);
  s.brake_bias = 0.62f;
  EXPECT_NEAR(vcu::BrakesFrame(s).brake_bias(), 0.62f, 0.005f);
}

}  // namespace
