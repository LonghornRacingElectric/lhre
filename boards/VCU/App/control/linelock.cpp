#include "control/linelock.hpp"

namespace vcu {

bool LineLockPolicy::Update(const LineLockInputs& in, const LineLockParams& p) {
  const bool eligible = p.enabled && in.drive && !in.hard_braking &&
                        in.soc_pct >= p.min_soc_pct && in.speed_valid &&
                        in.motor_rpm >= p.min_motor_rpm;
  if (!eligible) {
    requested_ = false;
    return false;
  }
  // Only isolate once the line is clear on both sides of the valve.
  if (!requested_ && (in.rear_pre_psi > p.engage_max_psi ||
                      in.rear_post_psi > p.engage_max_psi)) {
    return false;
  }
  requested_ = true;
  return true;
}

}  // namespace vcu
