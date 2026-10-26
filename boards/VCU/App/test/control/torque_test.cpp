#include <gtest/gtest.h>

#include <algorithm>

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
}

constexpr float kPi = 3.14159265358979f;

float Omega(float rpm) { return rpm * kPi / 30.0f; }

// A car whose inverter reports `true_ratio` x torque x omega as DC power.
// Current samples arrive every 10 ms; the controller runs every 3 ms.
struct PowerPlant {
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  vcu::PowerLimitParams pl = P().power_limit;
  float true_ratio = P().power_limit.dc_power_ratio;
  float last_torque = 0.0f;
  uint32_t now = 0;

  PowerPlant(float rpm) {
    in.pedal_travel = 1.0f;
    in.motor_rpm = rpm;
    in.motor_speed_valid = true;
    in.dt_ms = 3;
  }
  vcu::TorqueOutputs Tick() {
    now += 3;
    if (now % 10 < 3) {
      in.power_sample_ms = now;
      in.dc_bus_voltage = 500.0f;
      in.dc_bus_current =
          true_ratio * last_torque * Omega(in.motor_rpm) / 500.0f;
    }
    const auto out = path.Update(in, P().torque, pl, P().traction);
    last_torque = out.power_limited;
    return out;
  }
  float PowerW() const {
    return true_ratio * last_torque * Omega(in.motor_rpm);
  }
};

TEST(PowerLimit, CapsTorqueFromSpeedBeforeAnyFeedback) {
  PowerPlant car(4000.0f);
  car.pl.limit_w = 40000.0f;
  const auto out = car.Tick();
  EXPECT_NEAR(out.power_limited,
              40000.0f / (car.pl.dc_power_ratio * Omega(4000.0f)), 0.01f);
  EXPECT_LT(out.power_limited, out.lookup);
}

TEST(PowerLimit, TrimRemovesRatioErrorWithoutOvershoot) {
  PowerPlant car(4000.0f);
  car.pl.limit_w = 40000.0f;
  car.true_ratio = 1.05f * car.pl.dc_power_ratio;  // 5% above the assumption
  float peak = 0.0f;
  for (int i = 0; i < 1000; ++i) {  // 3 s
    car.Tick();
    peak = std::max(peak, car.PowerW());
  }
  EXPECT_NEAR(car.PowerW(), 40000.0f, 400.0f);
  EXPECT_LT(peak, 40000.0f * 1.05f + 1.0f);
}

TEST(PowerLimit, TrimMovesOnlyOnNewSamples) {
  PowerPlant car(4000.0f);
  car.pl.limit_w = 40000.0f;
  car.true_ratio = 1.05f * car.pl.dc_power_ratio;
  for (int i = 0; i < 50; ++i) car.Tick();
  const float trim = car.Tick().power_trim_w;
  // Same sample time: repeated ticks must not integrate it again.
  for (int i = 0; i < 5; ++i) {
    EXPECT_FLOAT_EQ(
        car.path.Update(car.in, P().torque, car.pl, P().traction).power_trim_w,
        trim);
  }
}

TEST(PowerLimit, TrimHoldsWhileTheCapIsNotLimiting) {
  PowerPlant car(4000.0f);
  car.in.pedal_travel = 0.3f;  // driver demand stays under the cap
  car.true_ratio = 2.0f;       // reported power far above the limit
  vcu::TorqueOutputs out;
  for (int i = 0; i < 300; ++i) out = car.Tick();
  EXPECT_FLOAT_EQ(out.power_trim_w, 0.0f);
  EXPECT_FLOAT_EQ(out.power_limited, out.lookup);
}

TEST(PowerLimit, NoCapNearStandstillOrWithoutSpeed) {
  PowerPlant car(50.0f);  // about 5 rad/s
  car.pl.limit_w = 1000.0f;
  auto out = car.Tick();
  EXPECT_FLOAT_EQ(out.power_limited, out.lookup);
  car.in.motor_rpm = 4000.0f;
  car.in.motor_speed_valid = false;
  out = car.Tick();
  EXPECT_FLOAT_EQ(out.power_limited, out.lookup);
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
