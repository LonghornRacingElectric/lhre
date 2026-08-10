#pragma once

#include "lhal/pwm.hpp"

namespace longhorn {

// RGB status LED on three PWM channels, one per color.
//
// The board owns timer configuration and channel start (lhal::stm32::Pwm's
// Start(), or CubeMX bring-up); this driver only writes duty cycles. On
// construction the LED lights mid-brightness white so a freshly booted board
// is visibly alive before any task runs.
class RgbLed {
 public:
  // Seconds for the rainbow animation to cycle back to its starting color.
  static constexpr float kRainbowCycleS = 5.0f;

  RgbLed(lhal::Pwm* red, lhal::Pwm* green, lhal::Pwm* blue);

  // Sets each color's brightness as a fraction in [0.0, 1.0].
  void Set(float r, float g, float b);
  void Off() { Set(0.0f, 0.0f, 0.0f); }

  // Advances the rainbow animation by dt_s seconds and writes the resulting
  // color. No-op once Disable() has been called.
  void Rainbow(float dt_s);

  // Stops Rainbow() from writing, so an error color applied with Set()
  // stays put even while the animation task keeps running.
  void Disable() { disabled_ = true; }

 private:
  lhal::Pwm* red_;
  lhal::Pwm* green_;
  lhal::Pwm* blue_;
  float phase_ = 0.0f;
  bool disabled_ = false;
};

}  // namespace longhorn
