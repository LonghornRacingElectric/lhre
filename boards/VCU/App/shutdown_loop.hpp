#pragma once

#include <cstdint>

#include "debounce.hpp"
#include "lhre_can_types.hpp"
#include "vcu_config.hpp"

namespace vcu {

// Shutdown loop state from the two taps either side of the BSPD relay K2:
// Shutdown_in_S upstream of the contacts, Shutdown_out_S downstream.
//
//   in  out  state
//   H   H    CLOSED           upstream live, K2 closed
//   H   L    BSPD_RELAY_OPEN  K2 open (BSPD latched, coil supply lost) or
//                             downstream shorted
//   L   L    UPSTREAM_OPEN    loop open before the VCU (or LV off)
//   L   H    IMPLAUSIBLE      back-feed or sense fault; treated as open
//
// Each tap gets hysteresis (high/low pin thresholds) then a debounce.
class ShutdownLoop {
 public:
  lhre::can::ShutdownLoopState Update(float in_pin_v, float out_pin_v,
                                      uint32_t now_ms, const ShutdownParams& p);

  lhre::can::ShutdownLoopState state() const { return state_; }
  bool closed() const {
    return state_ == lhre::can::ShutdownLoopState::kClosed;
  }

 private:
  static bool Hysteresis(bool last, float v, const ShutdownParams& p) {
    if (v >= p.high_pin_v) {
      return true;
    }
    if (v < p.low_pin_v) {
      return false;
    }
    return last;
  }

  bool in_high_ = false;
  bool out_high_ = false;
  Debouncer in_debounce_;
  Debouncer out_debounce_;
  bool sampled_ = false;
  uint32_t first_ms_ = 0;
  lhre::can::ShutdownLoopState state_ = lhre::can::ShutdownLoopState::kUnknown;
};

}  // namespace vcu
