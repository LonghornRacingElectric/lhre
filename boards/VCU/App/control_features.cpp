#include "control_features.hpp"

#include <cmath>

#include "lookup.hpp"

namespace vcu {

WheelControlResult WheelSlipController::Update(const WheelInputs& in,
                                               float demand_nm, uint32_t dt_ms,
                                               const WheelControlParams& p) {
  WheelControlResult out;
  out.valid = in.fresh && p.front_radius_m > 0 && p.rear_radius_m > 0 &&
              p.speed_floor_m_s > 0 && std::isfinite(demand_nm) && dt_ms <= 30;
  for (float speed : in.rad_s) out.valid &= std::isfinite(speed) && speed >= 0;
  if (out.valid) {
    const float front = in.rad_s[0] > in.rad_s[1] ? in.rad_s[0] : in.rad_s[1];
    const float rear_fast =
        in.rad_s[2] > in.rad_s[3] ? in.rad_s[2] : in.rad_s[3];
    const float rear_slow =
        in.rad_s[2] < in.rad_s[3] ? in.rad_s[2] : in.rad_s[3];
    // Use rear extremes so one spinning or locking wheel cannot hide in the
    // average.
    out.reference_m_s = front * p.front_radius_m;
    const float denominator = out.reference_m_s > p.speed_floor_m_s
                                  ? out.reference_m_s
                                  : p.speed_floor_m_s;
    out.drive_slip =
        (rear_fast * p.rear_radius_m - out.reference_m_s) / denominator;
    out.braking_slip =
        (out.reference_m_s - rear_slow * p.rear_radius_m) / denominator;
    out.regen_allowed = out.braking_slip <= p.max_braking_slip;
  }
  if (!p.enabled) {
    integral_nm_ = previous_nm_ = 0;
    recovering_ = false;
    out.positive_nm = demand_nm;
    return out;
  }
  if (!out.valid || demand_nm <= 0) {
    integral_nm_ = previous_nm_ = 0;
    recovering_ = false;
    return out;
  }
  const float dt = static_cast<float>(dt_ms) * 0.001f;
  const float error = out.drive_slip - p.target_drive_slip;
  integral_nm_ = Clamp(integral_nm_ + p.ki_nm_s * error * dt, 0.0f, demand_nm);
  const float trim = Clamp(p.kp_nm * error + integral_nm_, 0.0f, demand_nm);
  const float target = demand_nm - trim;
  // A normal throttle step is not recovery. Only a slip cut arms the ramp;
  // keep it armed until the cut has been fully restored at current demand.
  out.positive_nm =
      recovering_ ? Clamp(target, 0.0f, previous_nm_ + p.recovery_nm_s * dt)
                  : target;
  recovering_ = trim > 0 || out.positive_nm < demand_nm;
  previous_nm_ = out.positive_nm;
  return out;
}

RegenResult BrakeRegen::Update(const RegenInputs& in, const RegenParams& p) {
  RegenResult out;
  if (in.rear_pre_psi <= 100.0f && in.dc_a >= -p.max_current_a) {
    current_cut_ = false;
  }
  if (p.enabled && in.feedback_fresh && in.dc_a < -p.hard_cut_current_a) {
    current_cut_ = true;
  }
  out.current_cut = current_cut_;
  const bool inputs_valid =
      std::isfinite(in.rear_pre_psi) && std::isfinite(in.rear_post_psi) &&
      std::isfinite(in.motor_rpm) && std::isfinite(in.dc_v) &&
      std::isfinite(in.dc_a) && std::isfinite(in.max_cell_v) &&
      std::isfinite(in.pedal) && in.rear_pre_psi >= 0 && in.rear_post_psi >= 0;
  const bool params_valid = p.pack_resistance_ohm > 0 && p.series_cells > 0 &&
                            p.max_current_a > 0 && p.max_torque_nm > 0 &&
                            p.nm_per_psi > 0 &&
                            p.hard_cut_current_a >= p.max_current_a &&
                            p.min_motor_rpm > 0 && p.close_delay_ms > 0;
  const bool eligible = p.enabled && params_valid && inputs_valid && in.drive &&
                        in.feedback_fresh && !in.hard_braking &&
                        !current_cut_ && in.pedal <= 0.05f &&
                        in.motor_rpm >= p.min_motor_rpm && in.dc_v > 0 &&
                        in.dc_v < p.max_pack_v && in.max_cell_v > 0 &&
                        in.max_cell_v < p.max_cell_v && in.dt_ms <= 30;
  const float cell_pack_v = in.max_cell_v * p.series_cells;
  const float limiting_v = cell_pack_v > in.dc_v ? cell_pack_v : in.dc_v;
  const float headroom = p.max_pack_v - p.voltage_reserve_v - limiting_v;
  if (!eligible || headroom <= 0) {
    requested_ = false;
    closed_ms_ = 0;
    return out;
  }
  // Only isolate after pressure has released on both sides of the valve.
  if (!requested_ && (in.rear_pre_psi > p.engage_max_psi ||
                      in.rear_post_psi > p.engage_max_psi)) {
    return out;
  }
  requested_ = true;
  out.linelock_request = true;
  if (!in.linelock_out) {
    if (closed_ms_ != 0 || in.rear_pre_psi > p.engage_max_psi ||
        in.rear_post_psi > p.engage_max_psi) {
      requested_ = false;
      out.linelock_request = false;
    }
    closed_ms_ = 0;
    return out;
  }
  if (closed_ms_ > p.close_delay_ms) closed_ms_ = p.close_delay_ms;
  const uint32_t remaining = p.close_delay_ms - closed_ms_;
  closed_ms_ += in.dt_ms < remaining ? in.dt_ms : remaining;
  if (closed_ms_ < p.close_delay_ms) return out;

  const float current =
      Clamp(headroom / p.pack_resistance_ohm, 0.0f, p.max_current_a);
  const float omega = in.motor_rpm * 0.104719755f;
  if (omega <= 0) return out;
  const float pack_limit = in.dc_v * current / omega;
  const float cap = pack_limit < p.max_torque_nm ? pack_limit : p.max_torque_nm;
  // Subtract residual hydraulic braking from the regen demand.
  out.torque_nm =
      -Clamp((in.rear_pre_psi - in.rear_post_psi) * p.nm_per_psi, 0.0f, cap);
  return out;
}

bool LaunchRamp::Arm(bool drive, bool brake, float pedal, float rpm,
                     const LaunchParams& p) {
  if (running_ || !p.enabled || !drive || !brake || pedal != 0.0f ||
      std::fabs(rpm) > p.arm_max_rpm)
    return false;
  armed_ = true;
  running_ = false;
  inhibited_ = false;
  elapsed_ms_ = 0;
  return armed_;
}

float LaunchRamp::Update(float demand_nm, bool drive, bool brake, float pedal,
                         float rpm, uint32_t dt_ms, const LaunchParams& p) {
  if (!p.enabled || !drive) {
    armed_ = running_ = false;
    inhibited_ = false;
    return demand_nm;
  }
  if (dt_ms > 30 || rpm < -p.arm_max_rpm ||
      (running_ && (brake || pedal <= 0.0f))) {
    armed_ = running_ = false;
    inhibited_ = true;
  }
  if (inhibited_) {
    if (pedal <= 0.0f) inhibited_ = false;
    return 0;
  }
  if (!armed_) return demand_nm;
  if (brake) return 0;
  if (!running_ && pedal <= 0) return 0;
  running_ = true;
  const uint32_t remaining = p.timeout_ms - elapsed_ms_;
  elapsed_ms_ += dt_ms < remaining ? dt_ms : remaining;
  const float cap = p.initial_torque_nm +
                    p.ramp_nm_per_s * static_cast<float>(elapsed_ms_) * 0.001f;
  const float limited = Clamp(demand_nm, 0.0f, cap);
  // Timeout holds zero until pedal release; finish only when the ramp catches
  // demand.
  if (elapsed_ms_ >= p.timeout_ms) {
    armed_ = running_ = false;
    inhibited_ = true;
    return 0;
  }
  if (rpm >= p.finish_rpm && cap >= demand_nm) armed_ = running_ = false;
  return limited;
}

}  // namespace vcu
