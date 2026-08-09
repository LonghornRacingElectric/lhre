#pragma once

#include <cstdint>

namespace lhal {

// Monotonic time source. On STM32 this is the HAL tick.
class Clock {
 public:
  virtual ~Clock() = default;

  virtual uint32_t Millis() = 0;  // wraps at 2^32 ms (~49.7 days)
  virtual void DelayMs(uint32_t ms) = 0;
};

// Wrap-safe "has `interval_ms` elapsed since `since_ms`".
constexpr bool ElapsedMs(uint32_t now_ms, uint32_t since_ms,
                         uint32_t interval_ms) {
  return static_cast<uint32_t>(now_ms - since_ms) >= interval_ms;
}

}  // namespace lhal
