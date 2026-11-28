#pragma once

#include "config/params.hpp"

namespace vcu {

struct LineLockInputs {
  bool drive = false;  // in DRIVE with torque allowed
  bool hard_braking = false;
  float soc_pct = 0.0f;
  bool speed_valid = false;
  float motor_rpm = 0.0f;
  float rear_pre_psi = 0.0f;
  float rear_post_psi = 0.0f;
};

// Software line lock request: no driver button, just conditions. It asks for
// the valve when the car is in DRIVE, moving, charged enough and not in a
// hard stop, and only starts once the rear brake line is unpressurised. Once
// requested it holds through braking (that is the point of isolating the
// line) until a condition drops. The board's hard-braking gate still has the
// last word in hardware.
class LineLockPolicy {
 public:
  bool Update(const LineLockInputs& in, const LineLockParams& p);

 private:
  bool requested_ = false;
};

}  // namespace vcu
