#pragma once

#include "lhal/gpio.hpp"

namespace lhal::host {

// In-memory pin for tests and sims.
class Gpio final : public lhal::Gpio {
 public:
  void Write(bool level) override { level_ = level; }
  bool Read() const override { return level_; }

 private:
  bool level_ = false;
};

}  // namespace lhal::host
