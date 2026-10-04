#pragma once

#include <cstdint>

#include "debounce.hpp"
#include "vcu_config.hpp"

namespace vcu {

struct TorqueInputs {
  float pedal_travel = 0.0f;  // 0..1 after filter and deadzone
  float motor_rpm = 0.0f;
  bool motor_speed_valid = false;
  float dc_bus_voltage = 0.0f;  // inverter 0x0A7
  float dc_bus_current = 0.0f;  // inverter 0x0A6 (negative = regen)
  float ocv_min_cell_v = 0.0f;  // battery estimate
  uint32_t dt_ms = 0;
  // Hooks: wheel speeds (rad/s, FL FR RL RR) once USM frames are
  // subscribed; not used by the ported traction control.
  const float* wheel_speeds_rad_s = nullptr;
};

struct TorqueOutputs {
  float lookup = 0.0f;         // pedal map x torque table
  float derated = 0.0f;        // low-cell derate (telemetry unless applied)
  float power_limited = 0.0f;  // after the closed-loop trim
  float traction = 0.0f;       // after traction control
  float power_w = 0.0f;
};

// Positive torque request chain, ported from lhre-2026 TorqueMap.c,
// PowerLimit.c and TractionControl.c in model order. Fault and state gating
// happen in the app.
class TorquePath {
 public:
  TorqueOutputs Update(const TorqueInputs& in, const TorqueParams& torque,
                       const PowerLimitParams& pl, const TractionParams& tc);
  void Reset();

 private:
  float power_integral_ = 0.0f;
  float tc_integral_ = 0.0f;
  float tc_prev_omega_ = 0.0f;
  bool tc_seeded_ = false;
  Ema tc_alpha_filter_;
};

// Regen blending hook. lhre-2026's RegenLinelock.c (endurance only) is not
// ported: Rev A keeps the line lock request at 0 until regen blending is
// designed around BSE3 from the PDU.
struct RegenOutputs {
  float torque_nm = 0.0f;  // <= 0 when active
  bool linelock_request = false;
};
RegenOutputs RegenHook(float bse3_psi, bool bse3_available);

}  // namespace vcu
