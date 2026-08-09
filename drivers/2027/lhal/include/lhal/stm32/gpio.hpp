#pragma once

#include <cstdint>

#include "lhal/gpio.hpp"
#include "lhal/stm32/hal.hpp"

namespace lhal::stm32 {

// Wraps an already-configured pin. Configure the pin in board bring-up code
// (HAL_GPIO_Init), whether hand-written or CubeMX-generated.
class Gpio final : public lhal::Gpio {
 public:
  Gpio(GPIO_TypeDef* port, uint16_t pin) : port_(port), pin_(pin) {}

  void Write(bool level) override {
    HAL_GPIO_WritePin(port_, pin_, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
  }
  bool Read() const override {
    return HAL_GPIO_ReadPin(port_, pin_) == GPIO_PIN_SET;
  }
  void Toggle() override { HAL_GPIO_TogglePin(port_, pin_); }

 private:
  GPIO_TypeDef* port_;
  uint16_t pin_;
};

}  // namespace lhal::stm32
