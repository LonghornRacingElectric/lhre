#include "buzzer.hpp"

#include <cmath>

#include "lhal/system.hpp"
#include "lookup.hpp"

namespace vcu {

bool Buzzer::Update(uint32_t now_ms, bool loop_closed, uint32_t max_on_ms) {
  if (!active_) {
    return false;
  }
  const uint32_t limit = duration_ms_ < max_on_ms ? duration_ms_ : max_on_ms;
  if (!loop_closed || lhal::ElapsedMs(now_ms, start_ms_, limit)) {
    active_ = false;
  }
  return active_;
}

void BuildSineTable(uint16_t* out, size_t n, float amplitude) {
  constexpr float kTwoPi = 6.28318530718f;
  const float a = Clamp(amplitude, 0.0f, 1.0f) * 2047.0f;
  for (size_t i = 0; i < n; ++i) {
    const float phase = kTwoPi * static_cast<float>(i) / static_cast<float>(n);
    const float code = static_cast<float>(kDacMidScale) + a * std::sin(phase);
    out[i] = static_cast<uint16_t>(Clamp(code + 0.5f, 0.0f, 4095.0f));
  }
}

}  // namespace vcu
