#pragma once

#include "lhal/stm32/hal.hpp"
#include "lhal/system.hpp"

namespace lhal::stm32 {

// HAL tick-backed clock (1 ms resolution, set up by HAL_Init()).
class Clock final : public lhal::Clock {
 public:
  uint32_t Millis() override { return HAL_GetTick(); }
  void DelayMs(uint32_t ms) override { HAL_Delay(ms); }
};

}  // namespace lhal::stm32
