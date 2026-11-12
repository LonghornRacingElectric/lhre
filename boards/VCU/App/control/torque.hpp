#pragma once

#include <cstdint>

#include "config/params.hpp"
#include "longhorn/debounce.hpp"
#include "longhorn/math.hpp"

namespace vcu {

struct TorqueInputs {
  float pedal_travel = 0.0f;  // 0..1 after filter and deadzone
  float motor_rpm = 0.0f;
  bool motor_speed_valid = false;
  float dc_bus_voltage = 0.0f;   // inverter 0x0A7
  float dc_bus_current = 0.0f;   // inverter 0x0A6 (negative = regen)
  uint32_t power_sample_ms = 0;  // arrival time of the current sample
  float ocv_min_cell_v = 0.0f;   // battery estimate
  uint32_t dt_ms = 0;
  uint32_t speed_sample_ms = 0;  // arrival time of the chosen CAN speed sample
  bool speed_from_high_speed = false;  // which inverter frame supplied it
};

struct TorqueOutputs {
  float lookup = 0.0f;         // pedal map x torque table
  float derated = 0.0f;        // low-cell derate (telemetry unless applied)
  float power_limited = 0.0f;  // after the power cap
  float traction = 0.0f;       // after traction control
  float power_w = 0.0f;        // inverter-reported V x I
  float power_trim_w = 0.0f;
};

// Pedal map -> power limit -> acceleration limiter. App handles state/fault
// gating.
class TorquePath {
 public:
  TorqueOutputs Update(const TorqueInputs& in, const TorqueParams& torque,
                       const PowerLimitParams& pl, const TractionParams& tc);
  void Reset();

 private:
  float LimitPower(const TorqueInputs& in, const PowerLimitParams& pl,
                   float base, TorqueOutputs* out);

  // Kept across Reset(): it corrects a slow-drifting ratio, not one pull.
  float power_trim_w_ = 0.0f;
  uint32_t power_prev_sample_ms_ = 0;
  bool power_seeded_ = false;
  bool power_cap_active_ = false;
  float tc_integral_ = 0.0f;
  float tc_prev_omega_ = 0.0f;
  uint32_t tc_prev_sample_ms_ = 0;
  float tc_trim_ = 0.0f;
  bool tc_seeded_ = false;
  bool tc_prev_from_high_speed_ = false;
  longhorn::Ema tc_alpha_filter_;
};

}  // namespace vcu
