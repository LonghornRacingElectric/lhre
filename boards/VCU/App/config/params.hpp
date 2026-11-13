#pragma once

// Calibration and per-event parameters; commissioning notes are in README.md.

#include <cstddef>
#include <cstdint>

#include "lhre_can_types.hpp"

namespace vcu {

inline constexpr size_t kLookupPoints = 11;

struct AnalogParams {
  // Rev A front end: 5.1k series / 10k shunt into a TLV900x follower.
  float sensor_divider_ratio = 10.0f / (5.1f + 10.0f);
  // Shutdown taps: 100k / 13.7k from the 24 V loop.
  float shutdown_divider_ratio = 13.7f / (100.0f + 13.7f);
  // RM0440: VREFINT_CAL is measured at VDDA = 3.0 V.
  float vrefint_cal_vdda = 3.0f;
  // Used when VREFINT is missing or implausible.
  float nominal_vdda = 3.3f;
};

struct AppsParams {
  // TODO(verify): capture sensor-side pedal endpoints with /cal. T.4.2.3
  // requires the two sensors to have different transfer functions.
  float apps1_v_at_0 = 0.5f;
  float apps1_v_at_100 = 4.5f;
  float apps2_v_at_0 = 0.5f;
  float apps2_v_at_100 = 4.5f;
  // Sensor-side open/short limits.
  float window_low_v = 0.25f;
  float window_high_v = 4.75f;
  // T.4.2.4, T.4.2.5: over 10% deviation for over 100 ms cuts torque.
  float max_deviation = 0.10f;
  uint32_t implausible_ms = 100;

  float ema_alpha = 0.35f;
  // Driver knobs, with TorqueParams::touchiness: filtered pedal travel at
  // which torque starts and at which it is full. Torque demand is 0 below
  // throttle_start and 1 above throttle_full, linear between. Travel at or
  // below throttle_start also counts as a released pedal (RTD, regen,
  // launch). TODO(verify): the inherited 9% and 74%; check with /analog.
  float throttle_start = 0.09f;
  float throttle_full = 0.74f;
};

struct BseParams {
  // TODO(verify): 0.5-4.5 V, 3000 psi brake transducers.
  float v_at_0_psi = 0.5f;
  float v_at_full = 4.5f;
  float full_scale_psi = 3000.0f;
  float window_low_v = 0.25f;
  float window_high_v = 4.75f;
  // T.4.3.3: a BSE fault lasting over 100 ms cuts torque.
  uint32_t range_fault_ms = 100;

  float ema_alpha = 0.10f;
  float pressed_on_psi = 300.0f;
  float pressed_off_psi = 250.0f;
  float brake_light_psi = 30.0f;
  float brake_light_duty = 0.30f;
  // Brake bias holds its last value below this total (vcu_can.c:331-341).
  float bias_min_total_psi = 1000.0f;
  // EV.4.7: unfiltered normalized APPS travel, before throttle_start/full.
  float bot_pedal_on = 0.25f;
  float bot_pedal_off = 0.05f;
  // TODO(verify): match the BSPD trimmer (0.595-0.976 V at the pin).
  float hard_brake_pin_v = 0.80f;
  // The trimmer moves, so the firmware/hardware mismatch fault ignores the
  // band hard_brake_pin_v +/- this margin (the default spans the whole
  // trimmer range). It only judges the firmware view, never the BSPD.
  float hard_brake_margin_v = 0.20f;
  uint32_t hard_brake_mismatch_ms = 50;
};

struct SteeringParams {
  // 0.5-4.5 V = 0-100% rotation (Rev A steering sheet note).
  float v_at_0 = 0.5f;
  float v_at_100 = 4.5f;
  float window_low_v = 0.25f;
  float window_high_v = 4.75f;
  // TODO(verify): steering wrap and linkage calibration, inherited from 2026.
  float wrap_above = 0.8f;
  float pct_zero = 0.02f;
  float pct_span = 0.62f;
  float range_deg = 230.0f;
  float offset_deg = 7.0f;
};

struct ShutdownParams {
  // Schematic note: tap above 2.31 V (~19.2 V loop) is "high". The low
  // threshold adds hysteresis (inferred, ~16.6 V loop).
  float high_pin_v = 2.31f;
  float low_pin_v = 2.00f;
  uint32_t debounce_ms = 10;
};

struct DigitalParams {
  uint32_t brake_flag_debounce_ms = 10;
  uint32_t motor_debounce_ms = 15;
  uint32_t rtd_debounce_ms = 20;
};

struct DriveParams {
  // Require a fully released pedal: travel at or below throttle_start.
  float rtd_max_pedal = 0.0f;
  // TODO(verify): assumes a toggle RTD switch; use false for a momentary
  // button.
  bool rtd_release_exits_drive = true;
  // The PDU's BSE_3 gates RTD_WAIT and DRIVE, and its timeout cuts torque.
  // Only the bench motor spin turns this off.
  bool require_pdu = true;
  // The shutdown loop taps gate RTD_WAIT and keep DRIVE. Only the bench
  // motor spin, with no loop wired, turns this off.
  bool require_loop = true;
  // Faults (safety/faults.hpp bits) not raised or acted on. Empty on the car.
  uint32_t ignored_faults = 0;
};

struct BuzzerParams {
  // EV.10.7.2: 1-3 s at 80 dBA minimum. TODO(verify): measure the level on
  // the car.
  float frequency_hz = 1000.0f;
  float amplitude = 0.3f;  // fraction of DAC full scale
  uint32_t duration_ms = 1200;
  // Hard ceiling independent of the requested duration.
  uint32_t max_on_ms = 1500;
};

struct InverterParams {
  float torque_limit_nm = 230.0f;
  uint32_t fault_clear_delay_ms = 1000;
  uint16_t fault_clear_address = 20;
};

// Pedal to torque is one function instead of the 2026 pedal-map and
// torque-vs-rpm tables:
//
//   torque = available(rpm) * demand^touchiness * fade(rpm),
//            then capped at PowerLimitParams' P / (ratio * omega)
//   available = peak_torque_nm, or min(peak_torque_nm, pedal_power_w / omega)
//               when pedal_power_w is set
//
// where demand is AppsParams' throttle_start..throttle_full travel scaled to
// 0..1. The 2026 rpm table only approximated a ~65 kW cap at the motor; the
// power cap now does that job directly. fade(rpm) is 1 unless
// zero_torque_rpm is set, then it falls linearly to 0 between
// fade_start_rpm and zero_torque_rpm and stays 0 above.
struct TorqueParams {
  float peak_torque_nm = 210.0f;
  // Exponent on demand. 1 is linear; above 1 gives less torque for the same
  // small travel, so the pedal feels less touchy off the line. 2026 used 1.5.
  float touchiness = 1.5f;
  // 0 disables the fade. The car relies on the power cap and the inverter's
  // own overspeed limit; the motor spin set uses it.
  float fade_start_rpm = 0.0f;
  float zero_torque_rpm = 0.0f;
  // 0 disables. Otherwise demand scales the torque that this much motor power
  // allows, up to peak, so part-pedal torque falls with speed instead of
  // holding until the power cap.
  float pedal_power_w = 0.0f;
  // Low-cell derate on the OCV estimate (TorqueMap.c:78-85).
  float low_cell_derate_start_v = 3.2f;
  float low_cell_cutoff_v = 2.8f;
  // Preserve the 2026 behavior: derate is reported but not applied.
  bool apply_low_cell_derate = false;
};

// Torque cap from motor speed, plus a slow trim learned from inverter V x I.
struct PowerLimitParams {
  float limit_w = 70000.0f;
  // Inverter-reported Vdc x Idc per unit of torque feedback x omega, set at
  // the high end of the logged spread. Not a physical efficiency.
  float dc_power_ratio = 0.95f;
  float trim_ki_per_s = 2.0f;  // trim watts per watt-second of error
  float trim_max_w = 10000.0f;
  // Below this speed the cap is skipped; omega near 0 would divide by zero.
  float min_omega_rad_s = 10.0f;
};

struct TractionParams {
  // Legacy motor-acceleration limiter; wheel-slip control is configured
  // separately.
  bool enabled = false;
  float accel_filter_alpha = 0.6f;
  float alpha_threshold_rad_s2 = 30.0f;
  float kp = 0.5f;
  float ki = 5.0f;
  float max_trim_nm = 50.0f;
};

struct BatteryParams {
  float rest_current_a = 5.0f;
  uint32_t rest_time_ms = 1000;
  float cell_voltage_ema_alpha = 0.005f;
  float soe_min_cell_v = 2.8f;
  float soe_max_cell_v = 4.2f;
  float soe_from_cell_voltage[kLookupPoints] = {0,  1,  3,  6,  10, 17,
                                                28, 46, 68, 86, 100};
  // app_freertos.c:361-366: before the HVC reports cells.
  float missing_min_cell_v = 3.2f;
};

// 132s3p RS50. Zero current/resistance limits keep regen off until
// commissioned.
struct RegenParams {
  bool enabled = false;
  float nm_per_psi = 67.0f / 500.0f;
  float engage_max_psi = 10.0f;
  float min_motor_rpm = 219.49f;
  float max_torque_nm = 230.0f;
  float max_current_a = 0.0f;
  float hard_cut_current_a = 0.0f;
  // The hard current cut releases once rear pressure is back under this.
  float current_cut_release_psi = 100.0f;
  float max_cell_v = 4.05f;
  float max_pack_v =
      132.0f * 4.2f;  // RS50 cell ceiling, not live BMS permission
  float voltage_reserve_v = 6.0f;
  float pack_resistance_ohm = 0.0f;
  float series_cells = 132.0f;
  uint32_t parallel_cells = 3;
  uint32_t close_delay_ms = 200;
};

// Acceleration launch ramp; pedal, power and slip limits still apply.
struct LaunchParams {
  bool enabled = false;
  float initial_torque_nm = 30.0f;  // commissioning values, not track tuning
  float ramp_nm_per_s = 100.0f;
  float arm_max_rpm = 20.0f;
  float finish_rpm = 1000.0f;
  uint32_t timeout_ms = 5000;
};

// Software line lock (no driver button): requested when every condition below
// holds. Regen can also request it (RegenParams).
struct LineLockParams {
  bool enabled = false;  // TODO(verify): off until the PDU response is checked
  // TODO(tune): placeholder. Minimum state of charge to request line lock.
  float min_soc_pct = 20.0f;
  // Minimum motor speed (the same figure RegenParams uses, from lhre-2026).
  float min_motor_rpm = 219.49f;
  // Rear brake pressure must be under this on both sides to start.
  float engage_max_psi = 10.0f;
};

struct WheelControlParams {
  bool enabled = false;  // opt-in until slip targets/gains are measured
  // vehicle/vehicle.yml: same loaded wheel radius on both axles.
  float front_radius_m = 0.2045f;
  float rear_radius_m = 0.2045f;
  float speed_floor_m_s = 1.0f;
  float target_drive_slip = 0.10f;
  float max_braking_slip = 0.10f;
  float kp_nm = 100.0f;
  float ki_nm_s = 100.0f;
  float recovery_nm_s = 400.0f;
};

struct Params {
  lhre::can::EventMode event_mode = lhre::can::EventMode::kUnassigned;
  AnalogParams analog;
  AppsParams apps;
  BseParams bse;
  SteeringParams steering;
  ShutdownParams shutdown;
  DigitalParams digital;
  DriveParams drive;
  BuzzerParams buzzer;
  InverterParams inverter;
  TorqueParams torque;
  PowerLimitParams power_limit;
  TractionParams traction;
  BatteryParams battery;
  RegenParams regen;
  LineLockParams linelock;
  LaunchParams launch;
  WheelControlParams wheel_control;
};

inline constexpr lhre::can::EventMode kDefaultEventMode =
    lhre::can::EventMode::kEndurance;

// Unknown modes use the default parameters.
const Params& ParamsFor(lhre::can::EventMode mode);

}  // namespace vcu
