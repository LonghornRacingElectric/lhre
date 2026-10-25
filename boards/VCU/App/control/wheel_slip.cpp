#include "control/wheel_slip.hpp"

#include <cmath>

#include "longhorn/math.hpp"

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
  integral_nm_ =
      longhorn::Clamp(integral_nm_ + p.ki_nm_s * error * dt, 0.0f, demand_nm);
  const float trim =
      longhorn::Clamp(p.kp_nm * error + integral_nm_, 0.0f, demand_nm);
  const float target = demand_nm - trim;
  // A normal throttle step is not recovery. Only a slip cut arms the ramp;
  // keep it armed until the cut has been fully restored at current demand.
  out.positive_nm =
      recovering_
          ? longhorn::Clamp(target, 0.0f, previous_nm_ + p.recovery_nm_s * dt)
          : target;
  recovering_ = trim > 0 || out.positive_nm < demand_nm;
  previous_nm_ = out.positive_nm;
  return out;
}

}  // namespace vcu
