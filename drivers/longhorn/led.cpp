#include "longhorn/led.hpp"

#include <cmath>

namespace longhorn {
namespace {

// Rainbow brightness cap so the LED doesn't blind anyone at close range.
constexpr float kRainbowBrightness = 0.5f;

}  // namespace

RgbLed::RgbLed(lhal::Pwm* red, lhal::Pwm* green, lhal::Pwm* blue)
    : red_(red), green_(green), blue_(blue) {
  Set(0.5f, 0.5f, 0.5f);
}

void RgbLed::Set(float r, float g, float b) {
  red_->SetDuty(r);
  green_->SetDuty(g);
  blue_->SetDuty(b);
}

void RgbLed::Rainbow(float dt_s) {
  if (disabled_) {
    return;
  }

  // Phase walks 0..3, one unit per edge of the R -> G -> B -> R triangle.
  constexpr float kPhaseRange = 3.0f;
  phase_ =
      std::fmod(phase_ + dt_s * (kPhaseRange / kRainbowCycleS), kPhaseRange);

  float r = 0.0f;
  float g = 0.0f;
  float b = 0.0f;
  if (phase_ < 1.0f) {
    r = 1.0f - phase_;
    g = phase_;
  } else if (phase_ < 2.0f) {
    g = 2.0f - phase_;
    b = phase_ - 1.0f;
  } else {
    b = 3.0f - phase_;
    r = phase_ - 2.0f;
  }

  Set(r * kRainbowBrightness, g * kRainbowBrightness, b * kRainbowBrightness);
}

}  // namespace longhorn
