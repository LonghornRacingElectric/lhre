#pragma once

#include "lhal/stm32/hal.hpp"
#include "pack_sense.hpp"

namespace hvc::board {

// hvc::DiffAdc on one differential channel of an ST HAL ADC: each Read()
// polls kSamples conversions back to back and averages them. Init()
// calibrates the ADC for differential mode and selects `channel` (its
// negative input is the next channel up, e.g. IN11+ / IN12-), so the
// CubeMX rank-1 channel doesn't matter. Instances may share an ADC: each
// Read() reselects its channel.
class StmDiffAdc final : public DiffAdc {
 public:
  StmDiffAdc(ADC_HandleTypeDef* adc, uint32_t channel)
      : adc_(adc), channel_(channel) {}

  bool Init();
  bool Read(int32_t* code_x16) override;

 private:
  bool SelectChannel();

  static constexpr uint32_t kTimeoutMs = 2;
  // 16384 x ~1.7 us = ~28 ms per Read(). Long on purpose: the noise seen
  // on the bench scales with the signal (~1.5%, likely ripple on VREF+),
  // and a 0.4 ms window just sampled a random point of it. 28 ms spans a
  // full 50/60 Hz cycle and many switching-ripple cycles. A power of two
  // so the sum divides exactly into 1/16 counts; 16384 * 4095 fits int32.
  static constexpr int32_t kSamples = 16384;

  ADC_HandleTypeDef* adc_;
  uint32_t channel_;
};

}  // namespace hvc::board
