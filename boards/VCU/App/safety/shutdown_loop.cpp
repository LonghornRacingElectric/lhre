#include "safety/shutdown_loop.hpp"

namespace vcu {

using lhre::can::ShutdownLoopState;

ShutdownLoopState ShutdownLoop::Update(float in_pin_v, float out_pin_v,
                                       uint32_t now_ms,
                                       const ShutdownParams& p) {
  in_high_ = Hysteresis(in_high_, in_pin_v, p);
  out_high_ = Hysteresis(out_high_, out_pin_v, p);
  const bool in = in_debounce_.Update(in_high_, now_ms, p.debounce_ms);
  const bool out = out_debounce_.Update(out_high_, now_ms, p.debounce_ms);
  if (!sampled_) {
    sampled_ = true;
    first_ms_ = now_ms;
  }
  // Report UNKNOWN until both taps had a full debounce window to settle.
  if (!lhal::ElapsedMs(now_ms, first_ms_, p.debounce_ms)) {
    state_ = ShutdownLoopState::kUnknown;
  } else if (in && out) {
    state_ = ShutdownLoopState::kClosed;
  } else if (in) {
    state_ = ShutdownLoopState::kBspdRelayOpen;
  } else if (out) {
    state_ = ShutdownLoopState::kImplausible;
  } else {
    state_ = ShutdownLoopState::kUpstreamOpen;
  }
  return state_;
}

}  // namespace vcu
