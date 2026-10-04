#pragma once

#include <cstdint>

#include "lhal/status.hpp"

namespace lhal {

// A hardware watchdog that resets the MCU unless refreshed within its
// timeout. On STM32 the independent watchdog (IWDG) cannot be stopped once
// started, so Start() is one-way.
class Watchdog {
 public:
  virtual ~Watchdog() = default;

  // Starts the watchdog with the closest achievable timeout at or below
  // `timeout_ms`. Returns kError if the timeout is out of range.
  virtual Status Start(uint32_t timeout_ms) = 0;

  virtual void Refresh() = 0;
};

}  // namespace lhal
