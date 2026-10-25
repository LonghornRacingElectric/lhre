#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/torque.hpp"

namespace {

const vcu::Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

TEST(Torque, MapPowerLimitAndDerate) {
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 1.0f;
  in.motor_rpm = 0.0f;
  in.dt_ms = 3;
  in.ocv_min_cell_v = 3.0f;  // halfway into the 2.8-3.2 V derate
  vcu::TorqueOutputs out =
      path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_FLOAT_EQ(out.lookup, 210.0f);
  EXPECT_NEAR(out.derated, 105.0f, 0.01f);
  // Derate not applied (ported behavior), no power: no trim.
  EXPECT_FLOAT_EQ(out.power_limited, 210.0f);

  // Half pedal: 0.5^1.5 of the 6000 rpm row.
  in.pedal_travel = 0.5f;
  in.motor_rpm = 6000.0f;
  out = path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_NEAR(out.lookup, 103.0f * 0.35355f, 0.01f);

  // 80 kW against a 70 kW limit trims torque.
  in.pedal_travel = 1.0f;
  in.dc_bus_voltage = 500.0f;
  in.dc_bus_current = 160.0f;
  out = path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_LT(out.power_limited, out.lookup);
  EXPECT_GE(out.power_limited, 0.0f);
}

TEST(Torque, SkidpadParamsSaturateAt70Percent) {
  const vcu::Params& skid = vcu::ParamsFor(lhre::can::EventMode::kSkidpad);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 1.0f;
  in.dt_ms = 3;
  const vcu::TorqueOutputs out =
      path.Update(in, skid.torque, skid.power_limit, skid.traction);
  EXPECT_NEAR(out.lookup, 210.0f * 0.70f, 0.01f);
}

TEST(Traction, SampleTimingFilterAndHeldTrimSurviveRepeatedControlTicks) {
  const auto& p = vcu::ParamsFor(vcu::kDefaultEventMode);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.motor_speed_valid = true;
  in.pedal_travel = 1;
  in.dt_ms = 3;
  in.speed_sample_ms = 100;
  vcu::TractionParams tc;
  tc.enabled = true;
  tc.accel_filter_alpha = 0.1f;
  path.Update(in, p.torque, p.power_limit, tc);
  in.motor_rpm = 10;
  in.speed_sample_ms = 110;
  const auto first = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_LT(first.traction, first.power_limited);
  for (int i = 0; i < 3; ++i) {
    EXPECT_FLOAT_EQ(path.Update(in, p.torque, p.power_limit, tc).traction,
                    first.traction);
  }
  // A new unchanged speed is zero raw acceleration. The filtered value
  // stays above threshold; using raw acceleration would drop trim to zero.
  in.speed_sample_ms = 120;
  const auto filtered = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_LT(filtered.traction, filtered.power_limited);
  in.motor_speed_valid = false;
  const auto stale = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_EQ(stale.traction, stale.power_limited);
}

TEST(Traction, SpeedSourceSwitchIsNotAnAcceleration) {
  const auto& p = vcu::ParamsFor(vcu::kDefaultEventMode);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.motor_speed_valid = true;
  in.pedal_travel = 1;
  in.dt_ms = 3;
  in.speed_from_high_speed = true;
  in.speed_sample_ms = 100;
  vcu::TractionParams tc;
  tc.enabled = true;
  path.Update(in, p.torque, p.power_limit, tc);
  // The slower position frame reports a different speed with its own clock.
  in.speed_from_high_speed = false;
  in.motor_rpm = 1000;
  in.speed_sample_ms = 103;
  const auto out = path.Update(in, p.torque, p.power_limit, tc);
  EXPECT_EQ(out.traction, out.power_limited);
}

}  // namespace
