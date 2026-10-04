#include "torque.hpp"

#include <cmath>

#include "lookup.hpp"

namespace vcu {
namespace {

constexpr float kPi = 3.14159265358979f;

// TorqueMap.c:78-85: 1 above the start voltage, 0 at the cutoff, linear
// between.
float LowCellDerate(float ocv, const TorqueParams& p) {
  if (ocv >= p.low_cell_derate_start_v) {
    return 1.0f;
  }
  if (ocv <= p.low_cell_cutoff_v) {
    return 0.0f;
  }
  return (ocv - p.low_cell_cutoff_v) /
         (p.low_cell_derate_start_v - p.low_cell_cutoff_v);
}

}  // namespace

void TorquePath::Reset() {
  power_integral_ = 0.0f;
  tc_integral_ = 0.0f;
  tc_prev_omega_ = 0.0f;
  tc_seeded_ = false;
  tc_alpha_filter_.Reset();
}

TorqueOutputs TorquePath::Update(const TorqueInputs& in,
                                 const TorqueParams& torque,
                                 const PowerLimitParams& pl,
                                 const TractionParams& tc) {
  TorqueOutputs out;
  const float dt_s = static_cast<float>(in.dt_ms) * 0.001f;

  // Torque map (TorqueMap.c:67-85).
  const float apps = Clamp(in.pedal_travel, 0.0f, 1.0f);
  const float rpm = Clamp(std::fabs(in.motor_rpm), 0.0f, torque.max_rpm);
  const float available = Lookup1D(torque.power_limit_torque, kLookupPoints,
                                   0.0f, torque.max_rpm, rpm);
  const float remapped =
      Lookup1D(torque.pedal_map, kLookupPoints, 0.0f, 1.0f, apps);
  out.lookup = available * std::pow(Clamp(remapped, 0.0f, 1.0f),
                                    torque.pedal_curve_exponent);
  out.derated = out.lookup * LowCellDerate(in.ocv_min_cell_v, torque);

  // Closed-loop power limit (PowerLimit.c:16-39): one-sided PI trim on
  // the inverter's DC bus power.
  const float base = torque.apply_low_cell_derate ? out.derated : out.lookup;
  out.power_w = in.dc_bus_voltage * in.dc_bus_current;
  const float error = out.power_w - pl.limit_w;
  power_integral_ =
      Clamp(power_integral_ + error * dt_s, 0.0f, pl.integral_max);
  const float trim = Clamp(pl.kp * error + pl.ki * power_integral_, 0.0f, base);
  out.power_limited = base - trim;

  // Traction control (TractionControl.c:15-55), disabled by default. Uses
  // the measured step instead of the original hard-coded 3 ms.
  out.traction = out.power_limited;
  if (tc.enabled && in.motor_speed_valid && dt_s > 0.0f) {
    const float omega = in.motor_rpm * kPi / 30.0f;
    if (!tc_seeded_) {
      tc_seeded_ = true;
      tc_prev_omega_ = omega;
    }
    const float alpha = (omega - tc_prev_omega_) / dt_s;
    tc_prev_omega_ = omega;
    tc_alpha_filter_.Update(alpha, tc.accel_filter_alpha);
    const float err = alpha - tc.alpha_threshold_rad_s2;
    float tc_trim = 0.0f;
    if (err > 0.0f) {
      tc_integral_ += err * dt_s;
      tc_trim = tc.kp * err + tc.ki * tc_integral_;
    } else {
      tc_integral_ = 0.0f;
    }
    tc_trim = Clamp(tc_trim, 0.0f, tc.max_trim_nm);
    tc_trim = Clamp(tc_trim, 0.0f, out.power_limited);
    out.traction = out.power_limited - tc_trim;
  }
  return out;
}

RegenOutputs RegenHook(float bse3_psi, bool bse3_available) {
  (void)bse3_psi;
  (void)bse3_available;
  return {};
}

}  // namespace vcu
