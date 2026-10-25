#pragma once

#include <cstdint>

#include "config/params.hpp"

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

}  // namespace vcu
