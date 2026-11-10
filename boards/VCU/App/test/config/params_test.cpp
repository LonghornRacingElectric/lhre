// Per-event parameter sets. Each set is hand-edited on the bench and at
// competition, so these check the relations a quick edit can break: a
// window that no longer contains its sensor range, hysteresis turned
// inside out, a rules limit loosened, or a mode that hands out another
// mode's parameters.

#include "config/params.hpp"

#include <gtest/gtest.h>

#include <cstddef>

namespace {

using lhre::can::EventMode;

const EventMode kModes[] = {
    EventMode::kUnassigned, EventMode::kAcceleration, EventMode::kSkidpad,
    EventMode::kAutocross,  EventMode::kEndurance,    EventMode::kMotorSpin,
};

// The mode the car reports on CAN is the one whose parameters it runs.
TEST(Params, EachModeGetsItsOwnSet) {
  for (EventMode mode : kModes) {
    EXPECT_EQ(vcu::ParamsFor(mode).event_mode, mode) << static_cast<int>(mode);
  }
}

TEST(Params, UnknownModeFallsBackToTheDefaults) {
  const vcu::Params& defaults = vcu::ParamsFor(EventMode::kUnassigned);
  EXPECT_EQ(&vcu::ParamsFor(static_cast<EventMode>(99)), &defaults);
}

TEST(Params, BootModeIsEndurance) {
  EXPECT_EQ(vcu::ParamsFor(vcu::kDefaultEventMode).event_mode,
            EventMode::kEndurance);
}

// The ranges below are the sensor's open/short limits; a calibrated
// endpoint outside them would read as a range fault at full travel.
void ExpectWindowContains(float low, float at_0, float at_100, float high,
                          const char* what) {
  EXPECT_LT(low, at_0) << what;
  EXPECT_LT(at_0, at_100) << what;
  EXPECT_LT(at_100, high) << what;
}

TEST(Params, SensorEndpointsSitInsideTheirFaultWindows) {
  for (EventMode mode : kModes) {
    SCOPED_TRACE(static_cast<int>(mode));
    const vcu::Params& p = vcu::ParamsFor(mode);
    ExpectWindowContains(p.apps.window_low_v, p.apps.apps1_v_at_0,
                         p.apps.apps1_v_at_100, p.apps.window_high_v, "apps1");
    ExpectWindowContains(p.apps.window_low_v, p.apps.apps2_v_at_0,
                         p.apps.apps2_v_at_100, p.apps.window_high_v, "apps2");
    ExpectWindowContains(p.bse.window_low_v, p.bse.v_at_0_psi, p.bse.v_at_full,
                         p.bse.window_high_v, "bse");
    ExpectWindowContains(p.steering.window_low_v, p.steering.v_at_0,
                         p.steering.v_at_100, p.steering.window_high_v,
                         "steering");
  }
}

// FSAE rules limits, which no mode may loosen.
TEST(Params, RulesLimitsHoldInEveryMode) {
  for (EventMode mode : kModes) {
    SCOPED_TRACE(static_cast<int>(mode));
    const vcu::Params& p = vcu::ParamsFor(mode);
    EXPECT_LE(p.apps.max_deviation, 0.10f) << "T.4.2.4";
    EXPECT_LE(p.apps.implausible_ms, 100u) << "T.4.2.5";
    EXPECT_LE(p.bse.range_fault_ms, 100u) << "T.4.3.3";
    EXPECT_LE(p.bse.bot_pedal_on, 0.25f) << "EV.4.7.2";
    EXPECT_GE(p.buzzer.duration_ms, 1000u) << "EV.10.7.2";
    EXPECT_LE(p.buzzer.duration_ms, 3000u) << "EV.10.7.2";
    EXPECT_LE(p.buzzer.duration_ms, p.buzzer.max_on_ms);
    EXPECT_LE(p.buzzer.max_on_ms, 3000u) << "EV.10.7.2";
  }
}

// Hysteresis with on below off chatters or never releases.
TEST(Params, ThresholdPairsAreOrdered) {
  for (EventMode mode : kModes) {
    SCOPED_TRACE(static_cast<int>(mode));
    const vcu::Params& p = vcu::ParamsFor(mode);
    EXPECT_GE(p.apps.throttle_start, 0.0f);
    EXPECT_LT(p.apps.throttle_start, p.apps.throttle_full);
    EXPECT_LE(p.apps.throttle_full, 1.0f);
    EXPECT_LE(p.drive.rtd_max_pedal, p.apps.throttle_start);
    EXPECT_LT(p.bse.pressed_off_psi, p.bse.pressed_on_psi);
    EXPECT_LT(p.bse.bot_pedal_off, p.bse.bot_pedal_on);
    EXPECT_LT(p.shutdown.low_pin_v, p.shutdown.high_pin_v);
    EXPECT_LT(p.torque.low_cell_cutoff_v, p.torque.low_cell_derate_start_v);
    EXPECT_LT(p.battery.soe_min_cell_v, p.battery.soe_max_cell_v);
    EXPECT_LE(p.regen.max_current_a, p.regen.hard_cut_current_a);
    EXPECT_LE(p.bse.brake_light_psi, p.bse.pressed_off_psi);
  }
}

// A filter alpha of 0 freezes the signal; above 1 it oscillates.
TEST(Params, FilterAlphasAreUsable) {
  for (EventMode mode : kModes) {
    SCOPED_TRACE(static_cast<int>(mode));
    const vcu::Params& p = vcu::ParamsFor(mode);
    for (float alpha :
         {p.apps.ema_alpha, p.bse.ema_alpha, p.battery.cell_voltage_ema_alpha,
          p.traction.accel_filter_alpha}) {
      EXPECT_GT(alpha, 0.0f);
      EXPECT_LE(alpha, 1.0f);
    }
  }
}

TEST(Params, TorqueSettingsAreConsistent) {
  for (EventMode mode : kModes) {
    SCOPED_TRACE(static_cast<int>(mode));
    const vcu::Params& p = vcu::ParamsFor(mode);
    EXPECT_GT(p.torque.peak_torque_nm, 0.0f);
    // The inverter clips anything above its limit, so a higher peak would
    // make the top of the pedal dead.
    EXPECT_LE(p.torque.peak_torque_nm, p.inverter.torque_limit_nm);
    EXPECT_GT(p.torque.touchiness, 0.0f);
    EXPECT_LE(p.torque.pedal_power_w, p.power_limit.limit_w);
    EXPECT_GT(p.power_limit.dc_power_ratio, 0.0f);
    EXPECT_LE(p.power_limit.dc_power_ratio, 1.0f);
    EXPECT_GT(p.power_limit.min_omega_rad_s, 0.0f);
    if (p.torque.zero_torque_rpm > 0.0f) {
      EXPECT_GT(p.torque.fade_start_rpm, 0.0f);
      EXPECT_LT(p.torque.fade_start_rpm, p.torque.zero_torque_rpm);
    }
    if (p.regen.enabled) {
      EXPECT_LE(p.regen.max_torque_nm, p.inverter.torque_limit_nm);
    }
  }
}

TEST(Params, StateOfEnergyTableRunsZeroToFull) {
  for (EventMode mode : kModes) {
    SCOPED_TRACE(static_cast<int>(mode));
    const float* soe = vcu::ParamsFor(mode).battery.soe_from_cell_voltage;
    EXPECT_EQ(soe[0], 0.0f);
    EXPECT_EQ(soe[vcu::kLookupPoints - 1], 100.0f);
    for (size_t i = 1; i < vcu::kLookupPoints; ++i) {
      EXPECT_LE(soe[i - 1], soe[i]) << i;
    }
  }
}

// Motor spin runs an unloaded motor on the bench: the torque cap must hold
// on both sides of the command, and nothing may add torque on top of the
// pedal.
TEST(Params, MotorSpinIsCappedAndFadesOut) {
  const vcu::Params& p = vcu::ParamsFor(EventMode::kMotorSpin);
  const vcu::Params& car = vcu::ParamsFor(EventMode::kEndurance);
  EXPECT_LT(p.torque.peak_torque_nm, car.torque.peak_torque_nm);
  EXPECT_EQ(p.inverter.torque_limit_nm, p.torque.peak_torque_nm);
  EXPECT_GT(p.torque.zero_torque_rpm, 0.0f) << "the fade must be on";
  EXPECT_FALSE(p.regen.enabled);
  EXPECT_FALSE(p.launch.enabled);
  EXPECT_FALSE(p.traction.enabled);
  EXPECT_FALSE(p.wheel_control.enabled);
}

// Only motor spin lowers the inverter's own limit; a car mode that
// inherited it would be stuck at bench torque.
TEST(Params, CarModesKeepTheFullInverterLimit) {
  const float full =
      vcu::ParamsFor(EventMode::kUnassigned).inverter.torque_limit_nm;
  for (EventMode mode : {EventMode::kAcceleration, EventMode::kSkidpad,
                         EventMode::kAutocross, EventMode::kEndurance}) {
    EXPECT_EQ(vcu::ParamsFor(mode).inverter.torque_limit_nm, full)
        << static_cast<int>(mode);
    EXPECT_EQ(vcu::ParamsFor(mode).torque.zero_torque_rpm, 0.0f)
        << static_cast<int>(mode);
  }
}

}  // namespace
