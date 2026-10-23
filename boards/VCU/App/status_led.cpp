#include "status_led.hpp"

namespace vcu {
namespace {

constexpr float kLevel = 0.3f;  // brightness; the LED is close to the eye
constexpr uint32_t kBlinkMs = 250;
constexpr uint32_t kCodePauseMs = 1500;

bool SlowBlink(uint32_t now_ms) { return (now_ms / (2 * kBlinkMs)) % 2 == 0; }

// On for the first `code` of each (code + pause) cycle of blink slots.
bool CodeBlink(uint32_t code, uint32_t now_ms) {
  const uint32_t cycle = code * 2 * kBlinkMs + kCodePauseMs;
  const uint32_t t = now_ms % cycle;
  if (t >= code * 2 * kBlinkMs) {
    return false;
  }
  return (t / kBlinkMs) % 2 == 0;
}

}  // namespace

Rgb StatusColor(lhre::can::VcuState state, lhre::can::VcuFaultCode code,
                uint32_t now_ms) {
  using lhre::can::VcuState;
  const uint32_t n = static_cast<uint32_t>(code);
  switch (state) {
    case VcuState::kBspdTripped:
      return {kLevel, 0.0f, kLevel};
    case VcuState::kDrive:
      return {0.0f, kLevel, 0.0f};
    default:
      break;
  }
  if (n != 0) {
    return CodeBlink(n, now_ms) ? Rgb{kLevel, 0.0f, 0.0f} : Rgb{};
  }
  switch (state) {
    case VcuState::kInit:
      return {kLevel, kLevel, kLevel};
    case VcuState::kIdle:
      return {0.0f, 0.0f, kLevel};
    case VcuState::kPrecharge:
      return SlowBlink(now_ms) ? Rgb{kLevel, kLevel * 0.5f, 0.0f} : Rgb{};
    case VcuState::kTsActive:
      return {kLevel, kLevel * 0.5f, 0.0f};
    case VcuState::kRtdWait:
      return SlowBlink(now_ms) ? Rgb{0.0f, kLevel, 0.0f} : Rgb{};
    case VcuState::kFault:
      return {kLevel, 0.0f, 0.0f};
    default:
      return {};
  }
}

}  // namespace vcu
