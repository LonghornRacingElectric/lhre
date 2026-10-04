#pragma once

#include <cstdint>

#include "lhal/adc.hpp"
#include "vcu_config.hpp"

namespace vcu {

// Every analog channel, in raw counts, MCU pin volts, and the volts on the
// far side of the board's divider. The ADC reference is the 3.3 V rail
// (VREF+ = VDDA), not the sensors' +5V_AN, so readings are corrected with
// VREFINT each sample.
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
