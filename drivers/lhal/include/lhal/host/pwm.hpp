#pragma once

#include "lhal/pwm.hpp"

namespace lhal::host {

// In-memory PWM channel for tests and sims.
class Pwm final : public lhal::Pwm {
 public:
  void SetDuty(float duty) override {
    if (duty < 0.0f) {
      duty = 0.0f;
    } else if (duty > 1.0f) {
      duty = 1.0f;
    }
    duty_ = duty;
  }

  float duty() const { return duty_; }

 private:
  float duty_ = 0.0f;
};

}  // namespace lhal::host
