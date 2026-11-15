#pragma once

#include <cstddef>
#include <cstdint>

namespace lhal {

// A continuously-sampled bank of ADC channels: the hardware (timer-triggered
// conversions into a circular DMA buffer on STM32) keeps every channel's
// latest result fresh, and the application reads snapshots. Channel order,
// trigger rate, sampling time, and oversampling are configured by board
// bring-up code (or the backend); this interface covers runtime reads only.
class AdcScan {
 public:
  virtual ~AdcScan() = default;

  virtual size_t channel_count() const = 0;

  // Latest result of `channel` (0-based scan rank) in raw counts,
  // right-aligned. Out-of-range channels read 0.
  virtual uint16_t Read(size_t channel) const = 0;

  // Count corresponding to the ADC reference voltage (4095 for 12-bit).
  virtual uint16_t full_scale() const = 0;

  // Scans finished since start, so callers can tell frozen data from a
  // steady signal. 0 when the backend does not count.
  virtual uint32_t completed_scans() const { return 0; }
};

}  // namespace lhal
