#pragma once

#include "vcu_config.hpp"

namespace vcu {

struct WheelInputs {
  bool fresh = false;
  float rad_s[4] = {};  // FL, FR, RL, RR; Data bus USM frames
};

struct WheelControlResult {
  bool valid = false;
  float positive_nm = 0;
  float reference_m_s = 0;
  float drive_slip = 0;
  float braking_slip = 0;
  bool regen_allowed = false;
};

class WheelSlipController {
 public:
  WheelControlResult Update(const WheelInputs& in, float demand_nm,
                            uint32_t dt_ms, const WheelControlParams& p);

 private:
  float integral_nm_ = 0;
  float previous_nm_ = 0;
};

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

class LaunchRamp {
 public:
  bool Arm(bool drive, bool brake, float pedal, float rpm,
           const LaunchParams& p);
  float Update(float demand_nm, bool drive, bool brake, float pedal, float rpm,
               uint32_t dt_ms, const LaunchParams& p);
  bool armed() const { return armed_; }
  void Abort() {
    armed_ = running_ = false;
    inhibited_ = true;
  }

 private:
  bool armed_ = false;
  bool running_ = false;
  bool inhibited_ = false;
  uint32_t elapsed_ms_ = 0;
};

}  // namespace vcu
