#include "control/regen.hpp"

#include <cmath>

#include "longhorn/math.hpp"

namespace vcu {

RegenResult BrakeRegen::Update(const RegenInputs& in, const RegenParams& p) {
  RegenResult out;
  if (in.rear_pre_psi <= p.current_cut_release_psi &&
      in.dc_a >= -p.max_current_a) {
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
      longhorn::Clamp(headroom / p.pack_resistance_ohm, 0.0f, p.max_current_a);
  const float omega = in.motor_rpm * 0.104719755f;
  if (omega <= 0) return out;
  const float pack_limit = in.dc_v * current / omega;
  const float cap = pack_limit < p.max_torque_nm ? pack_limit : p.max_torque_nm;
  // Subtract residual hydraulic braking from the regen demand.
  out.torque_nm = -longhorn::Clamp(
      (in.rear_pre_psi - in.rear_post_psi) * p.nm_per_psi, 0.0f, cap);
  return out;
}

}  // namespace vcu
