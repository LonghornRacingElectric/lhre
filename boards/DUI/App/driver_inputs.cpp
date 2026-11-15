#include "driver_inputs.hpp"

namespace dui {

const char* InputName(Input input) {
  switch (input) {
    case Input::kButton1:
      return "BTN1";
    case Input::kButton2:
      return "BTN2";
    case Input::kTopLeftUp:
      return "TOP_L_UP";
    case Input::kTopLeftDown:
      return "TOP_L_DOWN";
    case Input::kTopRightUp:
      return "TOP_R_UP";
    case Input::kTopRightDown:
      return "TOP_R_DOWN";
    case Input::kBottomLeftUp:
      return "BOT_L_UP";
    case Input::kBottomLeftDown:
      return "BOT_L_DOWN";
    case Input::kBottomRightUp:
      return "BOT_R_UP";
    case Input::kBottomRightDown:
      return "BOT_R_DOWN";
  }
  return "?";
}

DriverInputs::DriverInputs(lhal::Gpio* const* pins) : pins_(pins) {}

uint16_t DriverInputs::Sample(uint32_t now_ms) {
  uint16_t changed = 0;
  for (size_t i = 0; i < kInputCount; ++i) {
    const auto bit = static_cast<uint16_t>(1u << i);
    lhal::Gpio* pin = pins_ != nullptr ? pins_[i] : nullptr;
    const bool raw = pin != nullptr && pin->Read();

    if (raw != ((candidate_ & bit) != 0)) {
      // Level moved: restart this line's stability window.
      candidate_ = static_cast<uint16_t>(candidate_ ^ bit);
      candidate_since_ms_[i] = now_ms;
    } else if (raw != ((state_ & bit) != 0) &&
               static_cast<uint32_t>(now_ms - candidate_since_ms_[i]) >
                   kDebounceMs) {
      state_ = static_cast<uint16_t>(state_ ^ bit);
      changed = static_cast<uint16_t>(changed | bit);
    }
  }
  return changed;
}

}  // namespace dui
