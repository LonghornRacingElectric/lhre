#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_TIM_MODULE_ENABLED

#include <cstdint>

#include "lhal/pwm.hpp"
#include "lhal/status.hpp"

namespace lhal::stm32 {

// Wraps one channel of an already-configured timer. Configure the timer and
// channel in board bring-up code (HAL_TIM_PWM_Init / ConfigChannel, whether
// hand-written or CubeMX-generated), then call Start() once before use.
class Pwm final : public lhal::Pwm {
 public:
  Pwm(TIM_HandleTypeDef* handle, uint32_t channel)
      : handle_(handle), channel_(channel) {}

  Status Start() { return ToStatus(HAL_TIM_PWM_Start(handle_, channel_)); }
  Status Stop() { return ToStatus(HAL_TIM_PWM_Stop(handle_, channel_)); }

  void SetDuty(float duty) override {
    if (duty < 0.0f) {
      duty = 0.0f;
    } else if (duty > 1.0f) {
      duty = 1.0f;
    }
    // The output is high while CNT < CCR, so CCR must reach ARR + 1 for a
    // true 100% duty (CCR == ARR still leaves one low tick per period).
    float period = static_cast<float>(__HAL_TIM_GET_AUTORELOAD(handle_) + 1);
    __HAL_TIM_SET_COMPARE(handle_, channel_,
                          static_cast<uint32_t>(duty * period + 0.5f));
  }

  TIM_HandleTypeDef* handle() { return handle_; }

 private:
  TIM_HandleTypeDef* handle_;
  uint32_t channel_;
};

}  // namespace lhal::stm32

#endif  // HAL_TIM_MODULE_ENABLED
