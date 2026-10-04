#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "lhal/adc.hpp"

namespace lhal::host {

// In-memory ADC bank for tests and sims: set what each channel reads.
class AdcScan final : public lhal::AdcScan {
 public:
  explicit AdcScan(size_t channels, uint16_t full_scale = 4095)
      : counts_(channels, 0), full_scale_(full_scale) {}

  size_t channel_count() const override { return counts_.size(); }
  uint16_t Read(size_t channel) const override {
    return channel < counts_.size() ? counts_[channel] : 0;
  }
  uint16_t full_scale() const override { return full_scale_; }

  // Test helpers -----------------------------------------------------------
  void Set(size_t channel, uint16_t counts) {
    if (channel < counts_.size()) {
      counts_[channel] = counts > full_scale_ ? full_scale_ : counts;
    }
  }
  // Sets the channel to the count for `volts` against `vref`.
  void SetVolts(size_t channel, float volts, float vref) {
    float counts = volts / vref * static_cast<float>(full_scale_) + 0.5f;
    if (counts < 0.0f) {
      counts = 0.0f;
    }
    Set(channel, static_cast<uint16_t>(counts));
  }

 private:
  std::vector<uint16_t> counts_;
  uint16_t full_scale_;
};

}  // namespace lhal::host
