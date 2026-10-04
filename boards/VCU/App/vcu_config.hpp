#pragma once

// Every VCU tunable in one place. Values carried over from the 2026 car cite
// their lhre-2026 source; values that come from the Rev A front end are
// derived from the schematic. Anything not confirmed on hardware is marked
// TODO(verify) and listed in the board README.
//
// Per-event sets (ParamsFor) copy the defaults and override what changed,
// matching lhre-2026's VCU_DEFAULT_PARAMS + {acceleration,skidpad,endurance}
// _params.h. The active set changes only outside DRIVE (VCU_MODE_COMMAND).

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
  // Sensor-side volts at 0% and 100% travel. TODO(verify): APPS part and
  // pedal endpoints; capture with the test image's /cal and update. Do not
  // reuse lhre-2026's counts (different front end).
  float apps1_v_at_0 = 0.5f;
  float apps1_v_at_100 = 4.5f;
  float apps2_v_at_0 = 0.5f;
  float apps2_v_at_100 = 4.5f;
  // Open/short windows in sensor volts, with margin outside the travel
  // range. Below the low edge reads as a disconnect.
  float window_low_v = 0.25f;
  float window_high_v = 4.75f;
  // T.4.2: >10 percentage points of deviation for >100 ms is an
  // implausibility; torque is zeroed. TODO(verify rule) text. lhre-2026
  // used 15% (default_params.h:22); the prompt's 10% governs.
  float max_deviation = 0.10f;
  uint32_t implausible_ms = 100;
  // lhre-2026 default_params.h:23-25 (per 3 ms step).
  float ema_alpha = 0.35f;
  float min_travel_deadzone = 0.09f;
  float max_travel_deadzone = 0.74f;
};

struct BseParams {
  // 0.5-4.5 V = 0-3000 psi, the transducer range lhre-2026 used on every
  // BSE (BSE.c:9-13, PDU bse3.c:9-11). TODO(verify) the Rev A sensors.
  float v_at_0_psi = 0.5f;
  float v_at_full = 4.5f;
  float full_scale_psi = 3000.0f;
  float window_low_v = 0.25f;
  float window_high_v = 4.75f;
  // Range faults cut torque once they persist this long (same budget as
  // APPS). TODO(verify rule) EV/T brake sensor wording.
  uint32_t range_fault_ms = 100;
  // lhre-2026 default_params.h:28-42.
  float ema_alpha = 0.10f;
  float pressed_on_psi = 300.0f;
  float pressed_off_psi = 250.0f;
  float brake_light_psi = 30.0f;
  float brake_light_duty = 0.30f;
  // Brake bias holds its last value below this total (vcu_can.c:331-341).
  float bias_min_total_psi = 1000.0f;
  // Brake-over-throttle (EV.4.7, TODO(verify rule)): brake pressed with
  // pedal above 25% cuts torque until the pedal drops below 5%
  // (BSE.c:41-53). lhre-2026 only reported it; Rev A cuts torque.
  float bot_pedal_on = 0.25f;
  float bot_pedal_off = 0.05f;
  // Firmware's hard-brake view for the cross-check against the BSPD
  // comparators, in MCU pin volts. The board trimmer sets 0.595-0.976 V.
  // TODO(verify) the trimmed set point.
  float hard_brake_pin_v = 0.80f;
  uint32_t hard_brake_mismatch_ms = 50;
};

struct SteeringParams {
  // 0.5-4.5 V = 0-100% rotation (Rev A steering sheet note).
  float v_at_0 = 0.5f;
  float v_at_100 = 4.5f;
  float window_low_v = 0.25f;
  float window_high_v = 4.75f;
  // lhre-2026 app_freertos.c:238-241, 341 ("TODO chud temp tuning"):
  // fraction above 0.8 wraps, angle = ((pct - zero) / span - 0.5) * range +
  // offset. TODO(verify) on the car; that sensor spanned 0.464-4.176 V.
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
  // RTD needs the pedal fully released: lhre-2026 PRNDL.c:21 required
  // accel_pedal_travel == 0 (after the deadzone).
  float rtd_max_pedal = 0.0f;
  // lhre-2026 left DRIVE when the RTD switch opened (PRNDL.c:26-28): a
  // toggle switch. TODO(verify) the Rev A switch type; set false for a
  // momentary button.
  bool rtd_release_exits_drive = true;
};

struct BuzzerParams {
  // lhre-2026: ~991 Hz full-swing square from the DUI (DUI tim.c, buzzer.c)
  // for 1200 ms after entering DRIVE (default_params.h:96). Rev A plays a
  // sine through the PAM8302. TODO(verify rule): 1-3 s, 80-90 dBA, tone.
  float frequency_hz = 1000.0f;
  float amplitude = 1.0f;  // fraction of DAC full scale
  uint32_t duration_ms = 1200;
  // Local ceiling independent of anything received over CAN (the DUI's
  // hardened buzzer used 1500 ms).
  uint32_t max_on_ms = 1500;
};

struct InverterParams {
  // lhre-2026 vcu_can.c:234-274.
  float torque_limit_nm = 230.0f;
  uint32_t fault_clear_delay_ms = 1000;
  uint16_t fault_clear_address = 20;
};

struct TorqueParams {
  // Available torque vs |motor rpm| at 0, 600, ..., 6000 rpm.
  float max_rpm = 6000.0f;
  float power_limit_torque[kLookupPoints] = {210, 210, 210, 210, 210, 192,
                                             166, 145, 129, 115, 103};
  // Pedal remap at 0, 0.1, ..., 1.0 travel, then raised to the exponent.
  float pedal_map[kLookupPoints] = {0.0f, 0.1f, 0.2f, 0.3f, 0.4f, 0.5f,
                                    0.6f, 0.7f, 0.8f, 0.9f, 1.0f};
  float pedal_curve_exponent = 1.5f;
  // Low-cell derate on the OCV estimate (TorqueMap.c:78-85).
  float low_cell_derate_start_v = 3.2f;
  float low_cell_cutoff_v = 2.8f;
  // lhre-2026 computed the derate but the power limit ignored it
  // (PowerLimit.c:36-38). Kept off to port behavior as-is.
  bool apply_low_cell_derate = false;
};

struct PowerLimitParams {
  // Closed-loop trim on inverter DC bus V x I (PowerLimit.c:16-39).
  float limit_w = 70000.0f;
  float kp = 0.006f;
  float ki = 0.6f;
  float integral_max = 20000.0f;
};

struct TractionParams {
  // Disabled, as on the 2026 car (TractionControl.c). Hook for wheel-speed
  // TC once USM wheel speeds are subscribed.
  bool enabled = false;
  float accel_filter_alpha = 0.6f;
  float alpha_threshold_rad_s2 = 30.0f;
  float kp = 0.5f;
  float ki = 5.0f;
  float max_trim_nm = 50.0f;
};

struct BatteryParams {
  // Battery.c:18-44 and default_params.h:84-92.
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
};

// The firmware default event set (lhre-2026 app_freertos.c:82 selected
// endurance).
inline constexpr lhre::can::EventMode kDefaultEventMode =
    lhre::can::EventMode::kEndurance;

// Parameter set for an event; kUnassigned and unknown values give the
// defaults.
const Params& ParamsFor(lhre::can::EventMode mode);

}  // namespace vcu
