#pragma once

#include <cstdint>

#include "vcu_adc.hpp"
#include "vcu_config.hpp"

namespace vcu {

// Tracks completed conversions, not changes in pedal voltage: a driver may
// legitimately hold the pedal still indefinitely.
class AdcProgress {
 public:
  bool Fresh(const vcu::AdcScan* adc, uint32_t now_ms) {
    if (adc == nullptr) return false;
    const uint32_t count = adc->completed_scans();
    if (count != previous_) {
      previous_ = count;
      last_ms_ = now_ms;
      seen_ = true;
    }
    return seen_ && static_cast<uint32_t>(now_ms - last_ms_) < 10;
  }

 private:
  uint32_t previous_ = 0;
  uint32_t last_ms_ = 0;
  bool seen_ = false;
};

// VREFINT corrects the 3.3 V ADC reference; sensors use the separate 5V_AN
// rail.
struct AnalogReadings {
  struct Channel {
    uint16_t counts = 0;
    float pin_v = 0.0f;
    float source_v = 0.0f;  // sensor (or shutdown loop) side
  };
  Channel apps1;
  Channel apps2;
  Channel bse1;
  Channel bse2;
  Channel steering;
  Channel shutdown_in;
  Channel shutdown_out;
  uint16_t vrefint_counts = 0;
  float vdda = 0.0f;
  bool vdda_from_vrefint = false;
};

// VDDA from the VREFINT reading; falls back to `analog.nominal_vdda` when
// the factory calibration or the reading is missing or implausible.
float ComputeVdda(uint16_t vrefint_counts, uint16_t vrefint_cal,
                  uint16_t full_scale, const AnalogParams& analog,
                  bool* from_vrefint);

// Snapshots both ADC banks (either may be null: channels read 0).
AnalogReadings ReadAnalog(const lhal::AdcScan* adc1, const lhal::AdcScan* adc2,
                          uint16_t vrefint_cal, const AnalogParams& analog);

}  // namespace vcu
