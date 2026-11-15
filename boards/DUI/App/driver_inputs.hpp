#pragma once

#include <cstddef>
#include <cstdint>

#include "lhal/gpio.hpp"

namespace dui {

// The ten driver input lines: two push buttons (SW1, SW2) and the UP and
// DOWN contacts of four momentary steering switches (SW5-SW8). Each line is
// active-high: the switch connects it to 3.3 V, and a 10 kΩ pull-down + 100
// nF on the board hold it at 0 V otherwise (switches.kicad_sch).
//
// The order is the wire order for the future CAN frame, so append only.
enum class Input : uint8_t {
  kButton1,
  kButton2,
  kTopLeftUp,
  kTopLeftDown,
  kTopRightUp,
  kTopRightDown,
  kBottomLeftUp,
  kBottomLeftDown,
  kBottomRightUp,
  kBottomRightDown,
};
inline constexpr size_t kInputCount = 10;

// Short name for logs and the /inputs command, e.g. "BTN1", "TOP_L_UP".
const char* InputName(Input input);

// Bit for `input` in DriverInputs::state() / Sample()'s change mask.
constexpr uint16_t InputBit(Input input) {
  return static_cast<uint16_t>(1u << static_cast<uint8_t>(input));
}

// Debounces the ten lines. Call Sample() every millisecond; a line's
// reported state changes only once its raw level has held steady for more
// than kDebounceMs (requirement: "stable for > 5 ms before transmitting").
// The board's RC filter already removes most contact bounce, so this mainly
// guards against slow edges and noise on long steering-wheel wiring.
//
// Every line starts released. Null pins read as released, so tests and a
// partly populated board work.
class DriverInputs {
 public:
  static constexpr uint32_t kDebounceMs = 5;

  // `pins` points at kInputCount entries, indexed by Input.
  explicit DriverInputs(lhal::Gpio* const* pins);

  // Reads every line and returns a mask of the lines whose debounced state
  // changed on this call (0 if none).
  uint16_t Sample(uint32_t now_ms);

  // Debounced state: bit i set = Input i pressed.
  uint16_t state() const { return state_; }
  bool pressed(Input input) const { return (state_ & InputBit(input)) != 0; }

 private:
  lhal::Gpio* const* pins_;
  uint16_t state_ = 0;
  // Last raw level seen per line, and when it last changed.
  uint16_t candidate_ = 0;
  uint32_t candidate_since_ms_[kInputCount] = {};
};

}  // namespace dui
