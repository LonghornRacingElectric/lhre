#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "lhal/dac.hpp"

namespace lhal::host {

// Records what the application asked the DAC to play.
class WaveformDac final : public lhal::WaveformDac {
 public:
  Status Play(const uint16_t* samples, size_t count,
              uint32_t sample_rate_hz) override {
    if (samples == nullptr || count == 0 || sample_rate_hz == 0) {
      return Status::kError;
    }
    samples_.assign(samples, samples + count);
    sample_rate_hz_ = sample_rate_hz;
    playing_ = true;
    ++play_count_;
    return Status::kOk;
  }
  void Stop(uint16_t idle_code) override {
    playing_ = false;
    idle_code_ = idle_code;
  }
  bool playing() const override { return playing_; }

  // Test helpers -----------------------------------------------------------
  const std::vector<uint16_t>& samples() const { return samples_; }
  uint32_t sample_rate_hz() const { return sample_rate_hz_; }
  uint32_t play_count() const { return play_count_; }
  uint16_t idle_code() const { return idle_code_; }

 private:
  std::vector<uint16_t> samples_;
  uint32_t sample_rate_hz_ = 0;
  uint32_t play_count_ = 0;
  uint16_t idle_code_ = 0;
  bool playing_ = false;
};

}  // namespace lhal::host
