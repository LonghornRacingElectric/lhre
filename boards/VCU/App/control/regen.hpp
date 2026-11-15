#pragma once

#include <cstdint>

#include "config/params.hpp"

namespace vcu {

struct RegenInputs {
  bool drive = false;
  bool feedback_fresh = false;  // includes live HVC cell limits and BSE3
  bool hard_braking = false;
  bool linelock_out = false;  // hardware gate readback, not valve feedback
  float pedal = 0.0f;
  float rear_pre_psi = 0.0f;
  float rear_post_psi = 0.0f;
  float motor_rpm = 0.0f;
  float dc_v = 0.0f;
  float dc_a = 0.0f;
  float max_cell_v = 0.0f;
  uint32_t dt_ms = 0;
};

struct RegenResult {
  bool linelock_request = false;
  float torque_nm = 0.0f;
  bool current_cut = false;
};

class BrakeRegen {
 public:
  RegenResult Update(const RegenInputs& in, const RegenParams& p);

 private:
  bool requested_ = false;
  bool current_cut_ = false;
  uint32_t closed_ms_ = 0;
};

}  // namespace vcu
