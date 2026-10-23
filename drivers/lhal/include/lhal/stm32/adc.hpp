#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_ADC_MODULE_ENABLED

#include <cstddef>
#include <cstdint>

#include "lhal/adc.hpp"

namespace lhal::stm32 {

// Wraps an ADC the board configured for scan conversions into a circular
// DMA buffer (DMA continuous requests on, external trigger or continuous
// mode, DMA channel linked in the MSP or by hand). Start() calibrates (where
// the family supports it) and starts the DMA; from then on the hardware
// keeps `buffer` fresh and Read() is a plain memory read.
class DmaAdc final : public lhal::AdcScan {
 public:
  // `buffer` holds one halfword per scan rank and must outlive the adapter
  // (it is the DMA target).
  DmaAdc(ADC_HandleTypeDef* hadc, volatile uint16_t* buffer, size_t channels,
         uint16_t full_scale = 4095)
      : hadc_(hadc),
        buffer_(buffer),
        channels_(channels),
        full_scale_(full_scale) {}

  DmaAdc(const DmaAdc&) = delete;
  DmaAdc& operator=(const DmaAdc&) = delete;

  Status Start() {
#if defined(ADC_SINGLE_ENDED)
    HAL_StatusTypeDef s = HAL_ADCEx_Calibration_Start(hadc_, ADC_SINGLE_ENDED);
    if (s != HAL_OK) {
      return ToStatus(s);
    }
#endif
    return ToStatus(HAL_ADC_Start_DMA(
        hadc_, reinterpret_cast<uint32_t*>(const_cast<uint16_t*>(buffer_)),
        static_cast<uint32_t>(channels_)));
  }

  size_t channel_count() const override { return channels_; }
  uint16_t Read(size_t channel) const override {
    return channel < channels_ ? buffer_[channel] : 0;
  }
  uint16_t full_scale() const override { return full_scale_; }

  // Escape hatch for anything LHAL doesn't cover.
  ADC_HandleTypeDef* handle() { return hadc_; }

 private:
  ADC_HandleTypeDef* hadc_;
  volatile uint16_t* buffer_;
  size_t channels_;
  uint16_t full_scale_;
};

}  // namespace lhal::stm32

#endif  // HAL_ADC_MODULE_ENABLED
