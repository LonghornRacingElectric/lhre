#include "Sil/sensors.hpp"

#include <cmath>

#include "longhorn/math.hpp"

namespace vcu::sil {

namespace {

constexpr float kPi = 3.14159265358979f;

// About 6 ADC counts of sensor travel. ADC rounding right at the wrap would
// flip a reading to the far end of the steering range.
constexpr float kSteeringWrapMargin = 0.002f;

}  // namespace

float BseVolts(float psi, const BseParams& p) {
  return p.v_at_0_psi + psi / p.full_scale_psi * (p.v_at_full - p.v_at_0_psi);
}

float SteeringVolts(float angle_deg, const SteeringParams& p, bool* clamped) {
  // ComputeSteering(): angle = ((pct - pct_zero) / pct_span - 0.5) * range
  // + offset, where pct is the travel after the wrap.
  float pct = ((angle_deg - p.offset_deg) / p.range_deg + 0.5f) * p.pct_span +
              p.pct_zero;
  const float hi = p.wrap_above - kSteeringWrapMargin;
  const float lo = p.wrap_above - 1.0f + kSteeringWrapMargin;
  *clamped = !std::isfinite(pct) || pct > hi || pct < lo;
  if (!std::isfinite(pct)) {
    pct = p.pct_zero;
  }
  pct = longhorn::Clamp(pct, lo, hi);
  if (pct < 0.0f) {
    pct += 1.0f;  // reads back as pct - 1 past wrap_above
  }
  return p.v_at_0 + pct * (p.v_at_100 - p.v_at_0);
}

int16_t MotorRpm(float rad_s, bool* clamped) {
  constexpr float kMax = 32767.0f;
  if (!std::isfinite(rad_s)) {
    *clamped = true;
    return 0;
  }
  const float rpm = rad_s * (30.0f / kPi);  // may overflow to +-inf
  *clamped = rpm > kMax || rpm < -kMax;
  return static_cast<int16_t>(std::lround(longhorn::Clamp(rpm, -kMax, kMax)));
}

float WheelSpeed(float rad_s, bool* clamped) {
  return FitField(std::fabs(rad_s), -32768, 32767, 0.01f, clamped);
}

float FitField(float value, int32_t raw_min, int32_t raw_max, float scale,
               bool* clamped) {
  if (!std::isfinite(value)) {
    *clamped = true;
    return 0.0f;
  }
  // The same division the setter does. Within half a count of either end the
  // value rounds to that end, which is encoding, not saturation, so it is
  // not flagged. It is still pinned to the end itself: just below an
  // unsigned field's zero, the setter's own rounding can reach -1.
  const float scaled = value / scale;
  const float lo = static_cast<float>(raw_min);
  const float hi = static_cast<float>(raw_max);
  *clamped = !(scaled > lo - 0.5f && scaled < hi + 0.5f);
  if (scaled >= lo && scaled <= hi) {
    return value;
  }
  return (scaled < lo ? lo : hi) * scale;
}

}  // namespace vcu::sil
