#pragma once

#include <atomic>
#include <cstdint>

#include "FreeRTOS.h"
#include "lhal/system.hpp"
#include "longhorn/led.hpp"
#include "task.h"

namespace longhorn {

// Single PWM writer, matching last year's independent 33 ms rainbow task.
// Override requests cross tasks atomically; the caller never writes PWM.
class RainbowTask {
 public:
  static constexpr uint32_t kPeriodMs = 33;
  RainbowTask(RgbLed* led, lhal::Clock* clock) : led_(led), clock_(clock) {}
  void Start() {
    if (led_ != nullptr) {
      xTaskCreateStatic(Entry, "rainbow", kStack, this, tskIDLE_PRIORITY + 2,
                        stack_, &tcb_);
    }
  }
  void Override(float r, float g, float b) {
    color_.store(0x1000000u | (Channel(r) << 16) | (Channel(g) << 8) |
                 Channel(b));
  }
  void Auto() { color_.store(0); }
  void Step(uint32_t now) {
    if (led_ == nullptr) return;
    if (!started_) {
      started_ = true;
      last_ms_ = now;
      return;
    }
    const uint32_t dt = now - last_ms_;
    if (dt < kPeriodMs) return;
    last_ms_ = now;
    const uint32_t color = color_.load();
    if (color & 0x1000000u) {
      led_->Set(((color >> 16) & 255) / 255.0f, ((color >> 8) & 255) / 255.0f,
                (color & 255) / 255.0f);
    } else {
      led_->Rainbow(static_cast<float>(dt) * 0.001f);
    }
  }

 private:
  static uint32_t Channel(float v) {
    return v <= 0 ? 0 : (v >= 1 ? 255 : static_cast<uint32_t>(v * 255 + 0.5f));
  }
  static void Entry(void* context) {
    auto* self = static_cast<RainbowTask*>(context);
    TickType_t wake = xTaskGetTickCount();
    for (;;) {
      self->Step(self->clock_->Millis());
      vTaskDelayUntil(&wake, pdMS_TO_TICKS(kPeriodMs));
    }
  }
  static constexpr configSTACK_DEPTH_TYPE kStack = 2 * configMINIMAL_STACK_SIZE;
  RgbLed* led_;
  lhal::Clock* clock_;
  std::atomic<uint32_t> color_{0};
  bool started_ = false;
  uint32_t last_ms_ = 0;
  StaticTask_t tcb_;
  StackType_t stack_[kStack];
};

}  // namespace longhorn
