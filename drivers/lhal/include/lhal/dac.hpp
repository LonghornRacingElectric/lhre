#pragma once

#include <cstddef>
#include <cstdint>

#include "lhal/status.hpp"

namespace lhal {

// A DAC channel that plays a buffer of samples on repeat at a fixed sample
// rate (timer-triggered DMA on STM32), e.g. a tone from a sine table. Pin,
// output buffer, and the trigger timer are configured by board bring-up code
// (or the backend).
class WaveformDac {
 public:
  virtual ~WaveformDac() = default;

  // Starts (or restarts) looping `samples` (right-aligned DAC codes) at
  // `sample_rate_hz`. `samples` must stay valid and unchanged until Stop().
  virtual Status Play(const uint16_t* samples, size_t count,
                      uint32_t sample_rate_hz) = 0;

  // Stops playback and parks the output at `idle_code`.
  virtual void Stop(uint16_t idle_code) = 0;

  virtual bool playing() const = 0;
};

}  // namespace lhal
