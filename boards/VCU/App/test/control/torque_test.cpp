#include <gtest/gtest.h>

#include <algorithm>
#include <vector>

#include "config/params.hpp"
#include "control/torque.hpp"

namespace {

// Autocross runs the bare pedal function; endurance adds pedal_power_w.
const vcu::Params& P() {
  return vcu::ParamsFor(lhre::can::EventMode::kAutocross);
}

constexpr float kPi = 3.14159265358979f;

float Omega(float rpm) { return rpm * kPi / 30.0f; }

TEST(Torque, PedalFunctionPeakAndDerate) {
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 1.0f;
  in.motor_rpm = 0.0f;
  in.dt_ms = 3;
  in.ocv_min_cell_v = 3.0f;  // halfway into the 2.8-3.2 V derate
  vcu::TorqueOutputs out =
      path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_FLOAT_EQ(out.pedal, 210.0f);
  EXPECT_NEAR(out.derated, 105.0f, 0.01f);
  // Derate not applied (ported behavior), no power: no trim.
  EXPECT_FLOAT_EQ(out.power_limited, 210.0f);

  // Half demand: 0.5^touchiness of peak, whatever the speed. Speed only
  // enters through the power cap.
  in.pedal_travel = 0.5f;
  in.motor_rpm = 6000.0f;
  out = path.Update(in, P().torque, P().power_limit, P().traction);
  EXPECT_NEAR(out.pedal, 210.0f * 0.35355f, 0.01f);
}

// Sweeps demand at one speed and returns each step's torque after the cap.
std::vector<float> Sweep(const vcu::Params& p, float rpm) {
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.motor_rpm = rpm;
  in.motor_speed_valid = true;
  in.dt_ms = 3;
  std::vector<float> torque;
  for (int i = 0; i <= 100; ++i) {
    in.pedal_travel = static_cast<float>(i) / 100.0f;
    torque.push_back(
        path.Update(in, p.torque, p.power_limit, p.traction).power_limited);
  }
  return torque;
}

TEST(Torque, MonotonicInDemandWithAndWithoutTheCap) {
  for (float rpm : {0.0f, 1000.0f, 3000.0f, 4500.0f, 6000.0f, 7000.0f}) {
    const auto t = Sweep(P(), rpm);
    EXPECT_EQ(t.front(), 0.0f) << rpm;
    for (size_t i = 1; i < t.size(); ++i) {
      EXPECT_GE(t[i], t[i - 1]) << rpm << " rpm, step " << i;
    }
  }
}

TEST(Torque, FullDemandIsPeakUntilThePowerCapTakesOver) {
  const auto& pl = P().power_limit;
  // The cap meets the 210 Nm peak at P / (ratio x peak).
  const float corner_rpm =
      pl.limit_w / (pl.dc_power_ratio * 210.0f) * 30.0f / kPi;
  EXPECT_FLOAT_EQ(Sweep(P(), 0.9f * corner_rpm).back(), 210.0f);
  for (float rpm : {1.1f * corner_rpm, 5000.0f, 6000.0f, 7000.0f}) {
    EXPECT_NEAR(Sweep(P(), rpm).back(),
                pl.limit_w / (pl.dc_power_ratio * Omega(rpm)), 0.01f)
        << rpm;
  }
}

TEST(Torque, MotorSpinCapsTorqueAndFadesItToZeroBeforeTheSpeedCeiling) {
  const vcu::Params& spin = vcu::ParamsFor(lhre::can::EventMode::kMotorSpin);
  EXPECT_EQ(spin.inverter.torque_limit_nm, spin.torque.peak_torque_nm);
  EXPECT_FLOAT_EQ(Sweep(spin, 0.0f).back(), 5.0f);
  EXPECT_FLOAT_EQ(Sweep(spin, 1200.0f).back(), 5.0f);
  EXPECT_NEAR(Sweep(spin, 1350.0f).back(), 2.5f, 0.01f);
  EXPECT_NEAR(Sweep(spin, -1350.0f).back(), 2.5f, 0.01f);
  for (float rpm : {1500.0f, 2000.0f, 6000.0f}) {
    for (float t : Sweep(spin, rpm)) EXPECT_EQ(t, 0.0f) << rpm;
  }
}

TEST(Torque, MotorSpinGivesNoTorqueWithoutAFreshSpeed) {
  const vcu::Params& spin = vcu::ParamsFor(lhre::can::EventMode::kMotorSpin);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 1.0f;
  in.motor_rpm = 0.0f;
  in.motor_speed_valid = false;
  in.dt_ms = 3;
  EXPECT_EQ(path.Update(in, spin.torque, spin.power_limit, spin.traction).pedal,
            0.0f);
  // Without a fade, an unknown speed is left to the app's feedback faults.
  EXPECT_FLOAT_EQ(
      path.Update(in, P().torque, P().power_limit, P().traction).pedal, 210.0f);
}

TEST(Torque, ReleasedPedalGivesNoTorqueForAnyTouchiness) {
  vcu::TorqueParams t = P().torque;
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.pedal_travel = 0.0f;
  in.dt_ms = 3;
  for (float touchiness : {0.0f, -1.0f, 1.0f}) {
    t.touchiness = touchiness;
    EXPECT_EQ(path.Update(in, t, P().power_limit, P().traction).pedal, 0.0f)
        << touchiness;
  }
}

TEST(Torque, EnduranceDemandScalesTheTorqueItsPowerAllows) {
  const vcu::Params& endu = vcu::ParamsFor(lhre::can::EventMode::kEndurance);
  const float p_w = endu.torque.pedal_power_w;
  ASSERT_GT(p_w, 0.0f);
  const float corner_rpm = p_w / 210.0f * 30.0f / kPi;
  EXPECT_FLOAT_EQ(Sweep(endu, 0.9f * corner_rpm).back(), 210.0f);
  for (float rpm : {1.1f * corner_rpm, 4500.0f, 6000.0f}) {
    const auto t = Sweep(endu, rpm);
    EXPECT_NEAR(t.back(), p_w / Omega(rpm), 0.01f) << rpm;
    // The 2026 table's shape: part pedal falls with speed too.
    EXPECT_NEAR(t[50], Sweep(P(), 0.0f)[50] * p_w / (210.0f * Omega(rpm)),
                0.01f)
        << rpm;
  }
}

TEST(Torque, EnduranceStaysWithin17NmOfThe2026Table) {
  const float table[] = {210, 210, 210, 210, 210, 192, 166, 145, 129, 115, 103};
  const vcu::Params& endu = vcu::ParamsFor(lhre::can::EventMode::kEndurance);
  for (float rpm = 0.0f; rpm <= 6500.0f; rpm += 50.0f) {
    const float old_nm = std::min(
        longhorn::Lookup1D(table, 11, 0.0f, 6000.0f, rpm),
        rpm > 100.0f ? endu.power_limit.limit_w /
                           (endu.power_limit.dc_power_ratio * Omega(rpm))
                     : 1e9f);
    EXPECT_NEAR(Sweep(endu, rpm).back(), old_nm, 17.0f) << rpm;
  }
}

TEST(Torque, SpeedFadeIsOffByDefault) {
  const auto& pl = P().power_limit;
  EXPECT_NEAR(Sweep(P(), 20000.0f).back(),
              pl.limit_w / (pl.dc_power_ratio * Omega(20000.0f)), 0.01f);
}

// A car whose inverter reports `true_ratio` x torque x omega as DC power.
// Current samples arrive every 100 ms, as INVERTER_CURRENT does at 10 Hz. The
// controller runs every 3 ms.
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
    if (now % 100 < 3) {
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
  EXPECT_LT(out.power_limited, out.pedal);
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
  EXPECT_FLOAT_EQ(out.power_limited, out.pedal);
}

TEST(PowerLimit, NoCapNearStandstillOrWithoutSpeed) {
  PowerPlant car(50.0f);  // about 5 rad/s
  car.pl.limit_w = 1000.0f;
  auto out = car.Tick();
  EXPECT_FLOAT_EQ(out.power_limited, out.pedal);
  car.in.motor_rpm = 4000.0f;
  car.in.motor_speed_valid = false;
  out = car.Tick();
  EXPECT_FLOAT_EQ(out.power_limited, out.pedal);
}

TEST(Torque, SkidpadParamsAreLinearTo70Percent) {
  const vcu::Params& skid = vcu::ParamsFor(lhre::can::EventMode::kSkidpad);
  vcu::TorquePath path;
  vcu::TorqueInputs in;
  in.dt_ms = 3;
  in.pedal_travel = 1.0f;
  EXPECT_NEAR(
      path.Update(in, skid.torque, skid.power_limit, skid.traction).pedal,
      210.0f * 0.70f, 0.01f);
  in.pedal_travel = 0.5f;
  EXPECT_NEAR(
      path.Update(in, skid.torque, skid.power_limit, skid.traction).pedal,
      0.5f * 210.0f * 0.70f, 0.01f);
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
