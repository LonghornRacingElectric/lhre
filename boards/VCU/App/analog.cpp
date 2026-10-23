#include "analog.hpp"

#include "vcu_io.hpp"

namespace vcu {
namespace {

// G474 datasheet: VDDA operating range for the ADC.
constexpr float kMinPlausibleVdda = 2.7f;
constexpr float kMaxPlausibleVdda = 3.6f;

AnalogReadings::Channel MakeChannel(uint16_t counts, float vdda,
                                    uint16_t full_scale, float ratio) {
  AnalogReadings::Channel c;
  c.counts = counts;
  c.pin_v = full_scale == 0 ? 0.0f
                            : static_cast<float>(counts) * vdda /
                                  static_cast<float>(full_scale);
  c.source_v = ratio > 0.0f ? c.pin_v / ratio : 0.0f;
  return c;
}

uint16_t ReadOr0(const lhal::AdcScan* adc, size_t rank) {
  return adc == nullptr ? 0 : adc->Read(rank);
}

}  // namespace

float ComputeVdda(uint16_t vrefint_counts, uint16_t vrefint_cal,
                  uint16_t full_scale, const AnalogParams& analog,
                  bool* from_vrefint) {
  *from_vrefint = false;
  if (vrefint_counts == 0 || vrefint_cal == 0 || full_scale == 0) {
    return analog.nominal_vdda;
  }
  // VREFINT_CAL is a 12-bit count; rescale for other resolutions.
  const float cal = static_cast<float>(vrefint_cal) *
                    static_cast<float>(full_scale) / 4095.0f;
  const float vdda =
      analog.vrefint_cal_vdda * cal / static_cast<float>(vrefint_counts);
  if (vdda < kMinPlausibleVdda || vdda > kMaxPlausibleVdda) {
    return analog.nominal_vdda;
  }
  *from_vrefint = true;
  return vdda;
}

AnalogReadings ReadAnalog(const lhal::AdcScan* adc1, const lhal::AdcScan* adc2,
                          uint16_t vrefint_cal, const AnalogParams& analog) {
  AnalogReadings r;
  const uint16_t fs1 = adc1 == nullptr ? 4095 : adc1->full_scale();
  const uint16_t fs2 = adc2 == nullptr ? 4095 : adc2->full_scale();
  r.vrefint_counts = ReadOr0(adc1, kAdc1Vrefint);
  r.vdda = ComputeVdda(r.vrefint_counts, vrefint_cal, fs1, analog,
                       &r.vdda_from_vrefint);
  const float s = analog.sensor_divider_ratio;
  const float l = analog.shutdown_divider_ratio;
  r.apps1 = MakeChannel(ReadOr0(adc1, kAdc1Apps1), r.vdda, fs1, s);
  r.apps2 = MakeChannel(ReadOr0(adc1, kAdc1Apps2), r.vdda, fs1, s);
  r.bse1 = MakeChannel(ReadOr0(adc1, kAdc1Bse1), r.vdda, fs1, s);
  r.bse2 = MakeChannel(ReadOr0(adc1, kAdc1Bse2), r.vdda, fs1, s);
  r.shutdown_out = MakeChannel(ReadOr0(adc1, kAdc1ShutdownOut), r.vdda, fs1, l);
  r.steering = MakeChannel(ReadOr0(adc2, kAdc2Steering), r.vdda, fs2, s);
  r.shutdown_in = MakeChannel(ReadOr0(adc2, kAdc2ShutdownIn), r.vdda, fs2, l);
  return r;
}

}  // namespace vcu
