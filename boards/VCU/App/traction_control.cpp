#include "traction_control.hpp"

#include <algorithm>

namespace vcu {

float TractionControl::Apply(float upstream_nm, float motor_speed_rad_s,
                             float dt_s) {
  const float previous_speed_rad_s =
      seeded_ ? previous_speed_rad_s_ : motor_speed_rad_s;
  seeded_ = true;
  previous_speed_rad_s_ = motor_speed_rad_s;

  const float alpha_rad_s2 = (motor_speed_rad_s - previous_speed_rad_s) / dt_s;
  const float error_rad_s2 = alpha_rad_s2 - kAlphaThresholdRadS2;
  float trim_nm = 0.0f;
  if (error_rad_s2 > 0.0f) {
    integral_ += error_rad_s2 * dt_s;
    trim_nm = kKp * error_rad_s2 + kKi * integral_;
  } else {
    integral_ = 0.0f;
  }
  trim_nm = std::min({trim_nm, kMaxTrimNm, upstream_nm});
  return upstream_nm - trim_nm;
}

}  // namespace vcu
