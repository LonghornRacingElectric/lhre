#pragma once
#include <cstdint>
namespace longhorn {
// Aperiodic messages cannot drive a rate gate. Clamp fast periods to 1 ms.
template <class Message>
constexpr uint32_t PeriodMs() {
  static_assert(Message::kFrequencyHz > 0.0f, "aperiodic message has no period");
  constexpr float ms = 1000.0f / Message::kFrequencyHz;
  static_assert(ms <= 4294967040.0f, "period does not fit uint32_t");
  return ms < 1.0f ? 1u : static_cast<uint32_t>(ms);
}
}  // namespace longhorn
