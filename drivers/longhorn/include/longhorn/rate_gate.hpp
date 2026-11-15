#pragma once

#include <cstdint>

namespace longhorn {

// Fixed-rate trigger for work inside a faster loop. Deadlines accumulate,
// so periods that are not whole loop ticks do not drift.
class RateGate {
 public:
  bool Due(uint32_t now_ms, uint32_t period_ms) {
    if (!started_) {
      started_ = true;
      next_ms_ = now_ms;
    }
    if (static_cast<int32_t>(now_ms - next_ms_) < 0) {
      return false;
    }
    next_ms_ += period_ms;
    // Don't burst to catch up after a stall.
    if (static_cast<int32_t>(now_ms - next_ms_) >= 0) {
      next_ms_ = now_ms + period_ms;
    }
    return true;
  }

 private:
  uint32_t next_ms_ = 0;
  bool started_ = false;
};

}  // namespace longhorn
