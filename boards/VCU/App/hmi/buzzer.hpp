#pragma once

#include <cstddef>
#include <cstdint>

#include "config/params.hpp"

namespace vcu {

// Stop at the duration/ceiling or when the shutdown loop opens.
class Buzzer {
 public:
  void Start(uint32_t now_ms, uint32_t duration_ms) {
    active_ = true;
    start_ms_ = now_ms;
    duration_ms_ = duration_ms;
  }
  void Stop() { active_ = false; }

  // Returns whether the sound (and the amp) should be on this tick.
  bool Update(uint32_t now_ms, bool loop_closed, uint32_t max_on_ms);

  bool active() const { return active_; }

 private:
  bool active_ = false;
  uint32_t start_ms_ = 0;
  uint32_t duration_ms_ = 0;
};

// One period of a sine centered on mid-scale, `amplitude` (0..1) of
// half-scale, as 12-bit DAC codes.
void BuildSineTable(uint16_t* out, size_t n, float amplitude);

inline constexpr uint16_t kDacMidScale = 2048;

}  // namespace vcu
