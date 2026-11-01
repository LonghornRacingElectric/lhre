#include "control/launch.hpp"

#include <cmath>

#include "longhorn/math.hpp"

namespace vcu {

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
  const float limited = longhorn::Clamp(demand_nm, 0.0f, cap);
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
