#pragma once

#include <cstdint>

#include "config/params.hpp"

namespace vcu {

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
