#pragma once

#include <cstdint>

namespace hvc {

// Pack+ voltage sense: pack+ -> 1M + 1M + 150R divider, the 150R tap into
// an isolated amplifier (gain 41, 1.44 V output common mode) whose
// differential output is read by a differential ADC channel (PC5 = +,
// PB2 = -, ADC2_IN11). LHAL has no ADC interface, so the app defines this
// narrow one; the board implements it (Board/diff_adc.*).
class DiffAdc {
 public:
  virtual ~DiffAdc() = default;

  // The average of a burst of conversions, in 1/16 ADC counts (so
  // 2048 * 16 = 0 V differential, 0 = -VREF, 4095 * 16 = +VREF).
  // Averaging is what gets below the ADC's one-count steps (~0.5 V of
  // pack). False on an ADC error.
  virtual bool Read(int32_t* code_x16) = 0;
};

// ADC code with 0 V on pack+. Ideally 2048, but the amplifier's input
// bias current through the 150R tap (nothing matching on its - input)
// leaves ~+0.214 V on its output at 0 V in. Measured on the bench.
// In 1/16 counts, like DiffAdc::Read.
inline constexpr int32_t kPackZeroCode = 2183 * 16;

// Raw differential code -> pack+ in millivolts:
//   Vdiff = (code - kPackZeroCode) / (2048 * 16) * VREF   (VREF = 3.3 V)
//   Vtap  = Vdiff / 41                          (amplifier gain)
//   Vpack = Vtap * (1M + 1M + 150R) / 150R      (divider)
//           * kPackTrimNum / kPackTrimDen       (measured gain trim)
// Gain trim, measured on the bench: untrimmed, 60 V read 58 V (the
// amplifier's input resistance loading the 150R tap, plus resistor and
// gain tolerances). With only that 60/58, 600 V read 603 V: the error
// grows above ~100 V, likely the 1M resistors' voltage coefficient. The
// extra 600/603 centers the trim on the pack's operating range, at the
// cost of ~0.3 V low around 60 V.
inline constexpr int64_t kPackTrimNum = 2000;  // (60 * 600) / 18
inline constexpr int64_t kPackTrimDen = 1943;  // (58 * 603) / 18
inline constexpr int64_t kPackMvNum =
    3300LL * 2000150 * kPackTrimNum;  // VREF mV * Rtotal * trim
inline constexpr int64_t kPackMvDen =
    2048LL * 16 * 41 * 150 * kPackTrimDen;  // half-scale (x16) * gain * Rtap
inline constexpr int32_t PackMillivolts(int32_t code) {
  return static_cast<int32_t>((code - kPackZeroCode) * kPackMvNum /
                              kPackMvDen);
}

// Pack current sense: a 250 uOhm shunt into the same isolated amplifier
// (gain 41), read differentially on ADC2_IN3 (PA6 = +) / IN4 (PA7 = -).
//   Vdiff  = (code - kCurrentZeroCode) / (2048 * 16) * VREF
//   Vshunt = Vdiff / 41
//   I      = Vshunt / 250 uOhm * kCurrentTrimNum / kCurrentTrimDen
// One raw ADC count is ~0.16 A; the multi-sample average gets below that.
// The ADC clips at +-VREF/2 (+-1.65 V), i.e. +-40 mV on the shunt, so the
// reading saturates at about +-161 A even though the amplifier's
// +-50 mV input would allow +-200 A.
// Zero, measured on the bench with the shunt shorted: 0.15-0.25 A
// untrimmed, i.e. ~+20 x16 counts (~10 mA each) of amplifier offset.
// Gain trim: least-squares fit through zero of 15.3 / 19.8 / 24.9 mV on
// the shunt (61.2 / 79.2 / 99.6 A) against what they read, ~0.994.
inline constexpr int32_t kCurrentZeroCode = 2048 * 16 + 20;
inline constexpr int64_t kCurrentTrimNum = 160;
inline constexpr int64_t kCurrentTrimDen = 161;
inline constexpr int64_t kCurrentMaNum =
    3300LL * 4000 * kCurrentTrimNum;  // VREF mV * (1 / 250 uOhm, in mA/mV)
inline constexpr int64_t kCurrentMaDen =
    2048LL * 16 * 41 * kCurrentTrimDen;  // half-scale (x16) * gain
inline constexpr int32_t CurrentMilliamps(int32_t code) {
  return static_cast<int32_t>((code - kCurrentZeroCode) * kCurrentMaNum /
                              kCurrentMaDen);
}

}  // namespace hvc
