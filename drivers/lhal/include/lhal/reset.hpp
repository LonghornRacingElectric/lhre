#pragma once

#include <cstdint>

namespace lhal {

// Why the MCU last came out of reset. Backends report the most specific
// cause: a watchdog or software reset also pulls NRST, so the pin flag is
// only reported when nothing else is set.
enum class ResetCause : uint8_t {
  kUnknown = 0,
  kPowerOn = 1,  // power-on / brown-out reset
  kPin = 2,      // external NRST (reset button, debugger)
  kSoftware = 3,
  kIndependentWatchdog = 4,
  kWindowWatchdog = 5,
  kLowPower = 6,
  kOptionByteLoad = 7,
};

constexpr const char* ToString(ResetCause cause) {
  switch (cause) {
    case ResetCause::kPowerOn:
      return "power-on";
    case ResetCause::kPin:
      return "pin";
    case ResetCause::kSoftware:
      return "software";
    case ResetCause::kIndependentWatchdog:
      return "iwdg";
    case ResetCause::kWindowWatchdog:
      return "wwdg";
    case ResetCause::kLowPower:
      return "low-power";
    case ResetCause::kOptionByteLoad:
      return "option-byte";
    case ResetCause::kUnknown:
      break;
  }
  return "unknown";
}

}  // namespace lhal
