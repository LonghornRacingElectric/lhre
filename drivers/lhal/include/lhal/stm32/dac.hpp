#pragma once

#include "lhal/stm32/hal.hpp"

#if defined(HAL_DAC_MODULE_ENABLED) && defined(HAL_TIM_MODULE_ENABLED)

#include <cstddef>
#include <cstdint>

#include "lhal/dac.hpp"

namespace lhal::stm32 {

// A DAC channel fed by circular DMA and paced by a basic timer's TRGO. The
// board configures the DAC channel's trigger to that timer's TRGO, the
// timer's master output to "update", and links a circular halfword DMA
// channel to the DAC handle. Play() retunes the timer for the requested
// sample rate.
class DmaDac final : public lhal::WaveformDac {
 public:
  DmaDac(DAC_HandleTypeDef* hdac, uint32_t channel, TIM_HandleTypeDef* htim)
      : hdac_(hdac), channel_(channel), htim_(htim) {}

  DmaDac(const DmaDac&) = delete;
  DmaDac& operator=(const DmaDac&) = delete;

  Status Play(const uint16_t* samples, size_t count,
              uint32_t sample_rate_hz) override {
    if (samples == nullptr || count == 0 || sample_rate_hz == 0) {
      return Status::kError;
    }
    StopTransfer();
    // Smallest prescaler that fits the 16-bit auto-reload.
    const uint32_t ticks = TimerClockHz() / sample_rate_hz;
    if (ticks == 0) {
      return Status::kError;
    }
    const uint32_t prescaler = (ticks - 1) / 0x10000u;
    const uint32_t reload = ticks / (prescaler + 1) - 1;
    __HAL_TIM_SET_PRESCALER(htim_, prescaler);
    __HAL_TIM_SET_AUTORELOAD(htim_, reload);
    __HAL_TIM_SET_COUNTER(htim_, 0);
    HAL_StatusTypeDef s = HAL_DAC_Start_DMA(
        hdac_, channel_,
        reinterpret_cast<uint32_t*>(const_cast<uint16_t*>(samples)),
        static_cast<uint32_t>(count), DAC_ALIGN_12B_R);
    if (s != HAL_OK) {
      return ToStatus(s);
    }
    s = HAL_TIM_Base_Start(htim_);
    playing_ = s == HAL_OK;
    return ToStatus(s);
  }

  void Stop(uint16_t idle_code) override {
    StopTransfer();
    HAL_DAC_SetValue(hdac_, channel_, DAC_ALIGN_12B_R, idle_code);
    HAL_DAC_Start(hdac_, channel_);
    // The channel is hardware-triggered, so the held value only reaches the
    // pin on a trigger: fire one by forcing a timer update event.
    htim_->Instance->EGR = TIM_EGR_UG;
  }

  bool playing() const override { return playing_; }

  DAC_HandleTypeDef* handle() { return hdac_; }

 private:
  void StopTransfer() {
    HAL_TIM_Base_Stop(htim_);
    HAL_DAC_Stop_DMA(hdac_, channel_);
    playing_ = false;
  }

  // Timer kernel clock: PCLKx, doubled when that APB prescaler is not 1.
  uint32_t TimerClockHz() const {
    const bool apb2 =
        reinterpret_cast<uintptr_t>(htim_->Instance) >= APB2PERIPH_BASE &&
        reinterpret_cast<uintptr_t>(htim_->Instance) < AHB1PERIPH_BASE;
    if (apb2) {
      const uint32_t pclk = HAL_RCC_GetPCLK2Freq();
      return (RCC->CFGR & RCC_CFGR_PPRE2_2) != 0 ? pclk * 2 : pclk;
    }
    const uint32_t pclk = HAL_RCC_GetPCLK1Freq();
    return (RCC->CFGR & RCC_CFGR_PPRE1_2) != 0 ? pclk * 2 : pclk;
  }

  DAC_HandleTypeDef* hdac_;
  uint32_t channel_;
  TIM_HandleTypeDef* htim_;
  bool playing_ = false;
};

}  // namespace lhal::stm32

#endif  // HAL_DAC_MODULE_ENABLED && HAL_TIM_MODULE_ENABLED
