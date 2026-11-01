#include "longhorn/led.hpp"

#include <cmath>

namespace longhorn {

RgbLed::RgbLed(lhal::Pwm* red, lhal::Pwm* green, lhal::Pwm* blue,
               float rainbow_brightness, float boot_brightness)
    : red_(red), green_(green), blue_(blue), brightness_(rainbow_brightness) {
  Set(boot_brightness, boot_brightness, boot_brightness);
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

  Set(r * brightness_, g * brightness_, b * brightness_);
}

}  // namespace longhorn
