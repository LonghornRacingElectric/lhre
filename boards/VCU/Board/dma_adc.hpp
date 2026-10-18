#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_ADC_MODULE_ENABLED

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "vcu_adc.hpp"

namespace vcu_board {
using lhal::Status;
using lhal::stm32::ToStatus;

// Wraps an ADC the board configured for scan conversions into a circular
// DMA buffer (DMA continuous requests on, external trigger or continuous
// mode, DMA channel linked in the MSP or by hand). Start() calibrates (where
// the family supports it) and starts the DMA; from then on the hardware
// keeps `buffer` fresh and Read() is a plain memory read.
class DmaAdc final : public vcu::AdcScan {
 public:
  // `buffer` holds one halfword per scan rank and must outlive the adapter
  // (it is the DMA target).
  DmaAdc(ADC_HandleTypeDef* hadc, volatile uint16_t* buffer, size_t channels,
         uint16_t full_scale = 4095,
         const std::atomic<uint32_t>* completed_scans = nullptr)
      : hadc_(hadc),
        buffer_(buffer),
        channels_(channels),
        full_scale_(full_scale),
        completed_scans_(completed_scans) {}

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

  uint32_t completed_scans() const override {
    return completed_scans_ == nullptr ? 0 : completed_scans_->load();
  }

  // Escape hatch for anything LHAL doesn't cover.
  ADC_HandleTypeDef* handle() { return hadc_; }

 private:
  ADC_HandleTypeDef* hadc_;
  volatile uint16_t* buffer_;
  size_t channels_;
  uint16_t full_scale_;
  const std::atomic<uint32_t>* completed_scans_;
};

}  // namespace vcu_board

#endif  // HAL_ADC_MODULE_ENABLED
