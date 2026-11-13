#pragma once

#include <cstdint>

#include "lhal/watchdog.hpp"

namespace lhal::host {

// Counts starts and refreshes; tests advance their own clock and compare
// against last_refresh_ms() to assert a missed deadline.
class Watchdog final : public lhal::Watchdog {
 public:
  Status Start(uint32_t timeout_ms) override {
    if (timeout_ms == 0) {
      return Status::kError;
    }
    started_ = true;
    timeout_ms_ = timeout_ms;
    return Status::kOk;
  }
  void Refresh() override { ++refresh_count_; }

  // Test helpers -----------------------------------------------------------
  bool started() const { return started_; }
  uint32_t timeout_ms() const { return timeout_ms_; }
  uint32_t refresh_count() const { return refresh_count_; }

 private:
  bool started_ = false;
  uint32_t timeout_ms_ = 0;
  uint32_t refresh_count_ = 0;
};

}  // namespace lhal::host
