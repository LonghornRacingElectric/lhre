#pragma once

namespace lhal {

// A single PWM output channel. Timer frequency, period, and pin muxing are
// configured by board bring-up code (or the backend); this interface covers
// runtime duty-cycle control only.
class Pwm {
 public:
  virtual ~Pwm() = default;

  // duty is the high fraction of the period in [0.0, 1.0]; out-of-range
  // values are clamped.
  virtual void SetDuty(float duty) = 0;
};

}  // namespace lhal
