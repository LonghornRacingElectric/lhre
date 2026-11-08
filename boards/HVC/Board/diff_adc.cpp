#include "diff_adc.hpp"

namespace hvc::board {

bool StmDiffAdc::Init() {
  // Differential mode has its own calibration factor; run it before the
  // ADC is enabled.
  if (HAL_ADCEx_Calibration_Start(adc_, ADC_DIFFERENTIAL_ENDED) != HAL_OK) {
    return false;
  }
  return SelectChannel();
}

bool StmDiffAdc::SelectChannel() {
  ADC_ChannelConfTypeDef config = {};
  config.Channel = channel_;
  config.Rank = ADC_REGULAR_RANK_1;
  // 47.5 cycles at 36 MHz (~1.3 us): plenty for the amplifier's output,
  // unlike CubeMX's 2.5-cycle default.
  config.SamplingTime = ADC_SAMPLETIME_47CYCLES_5;
  config.SingleDiff = ADC_DIFFERENTIAL_ENDED;
  config.OffsetNumber = ADC_OFFSET_NONE;
  return HAL_ADC_ConfigChannel(adc_, &config) == HAL_OK;
}

bool StmDiffAdc::Read(int32_t* code_x16) {
  // Several StmDiffAdcs can share one ADC (pack voltage and current are
  // both on ADC2), so each Read() selects its own channel first.
  if (!SelectChannel()) {
    return false;
  }
  int32_t sum = 0;
  for (int32_t i = 0; i < kSamples; ++i) {
    if (HAL_ADC_Start(adc_) != HAL_OK) {
      return false;
    }
    const bool ok = HAL_ADC_PollForConversion(adc_, kTimeoutMs) == HAL_OK;
    if (ok) {
      sum += static_cast<int32_t>(HAL_ADC_GetValue(adc_));
    }
    HAL_ADC_Stop(adc_);
    if (!ok) {
      return false;
    }
  }
  *code_x16 = sum / (kSamples / 16);
  return true;
}

}  // namespace hvc::board
