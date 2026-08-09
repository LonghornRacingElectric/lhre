#pragma once

#include <chrono>
#include <thread>

#include "lhal/system.hpp"

namespace lhal::host {

// Manually-advanced clock for deterministic tests.
class TestClock final : public lhal::Clock {
 public:
  uint32_t Millis() override { return now_ms_; }
  void DelayMs(uint32_t ms) override { now_ms_ += ms; }
  void Advance(uint32_t ms) { now_ms_ += ms; }

 private:
  uint32_t now_ms_ = 0;
};

// Wall-clock-backed clock for interactive sims.
class SystemClock final : public lhal::Clock {
 public:
  SystemClock() : start_(std::chrono::steady_clock::now()) {}

  uint32_t Millis() override {
    return static_cast<uint32_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - start_)
            .count());
  }
  void DelayMs(uint32_t ms) override {
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
  }

 private:
  std::chrono::steady_clock::time_point start_;
};

}  // namespace lhal::host
