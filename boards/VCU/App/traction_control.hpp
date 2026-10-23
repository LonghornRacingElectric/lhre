#pragma once

namespace vcu {

// Motor-shaft acceleration traction control, ported from lhre-2026 PR #298
// (VCU/model/components/TractionControl.c) with its acceleration_params.h
// tuning. A PI trim grows while the shaft accelerates faster than
// kAlphaThresholdRadS2 and resets as soon as it does not.
//
// Two deliberate differences from #298: the acceleration is taken over the
// measured step instead of a hard-coded 3 ms, so it stays right when the
// caller's period is not 3 ms (the SIL steps every 10 ms); and the first
// sample only seeds the speed instead of differentiating from 0 rad/s.
class TractionControl {
 public:
  static constexpr float kAlphaThresholdRadS2 = 30.0f;
  static constexpr float kKp = 0.5f;
  static constexpr float kKi = 5.0f;
  static constexpr float kMaxTrimNm = 50.0f;

  // Returns upstream_nm minus the trim. The trim is at most kMaxTrimNm and
  // at most upstream_nm, so traction control never adds torque (TC-01).
  float Apply(float upstream_nm, float motor_speed_rad_s, float dt_s);

 private:
  bool seeded_ = false;
  float previous_speed_rad_s_ = 0.0f;
  float integral_ = 0.0f;
};

}  // namespace vcu
