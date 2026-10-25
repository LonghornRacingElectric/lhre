#pragma once

#include <atomic>
#include <cstdint>
#include <cstring>

#include "dfu.hpp"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"
#include "longhorn/led.hpp"
#include "longhorn/shell.hpp"

namespace vcu {

// Minimal board bring-up: no vehicle inputs, scheduler or task prerequisites.
class Mvp {
 public:
  Mvp(lhal::Clock* clock, lhal::Uart* usb, longhorn::RgbLed* led,
      void (*enter_dfu)())
      : clock_(clock),
        led_(led),
        enter_dfu_(enter_dfu),
        shell_(usb, clock, "VCU-MVP") {
    shell_.AddCommand(
        {"led", "red|green|blue|off|auto (no args: status)", &Led, this});
    shell_.AddCommand({"dfu", "enter ROM USB bootloader", &Dfu, this});
    if (!usb->SetRxObserver(&Receive, this)) {
      shell_.SetByteHandler(&Byte, this);
    }
    last_led_ms_ = clock_->Millis();
  }

  bool AddCommand(const longhorn::ShellCommand& command) {
    return shell_.AddCommand(command);
  }

  void Step() {
    if (EnterDfu()) return;
    shell_.Poll();
    if (EnterDfu()) return;
    const uint32_t now = clock_->Millis();
    const uint32_t dt = now - last_led_ms_;
    if (dt >= 33) {
      last_led_ms_ = now;
      if (rainbow_) {
        led_->Rainbow(static_cast<float>(dt) * 0.001f);
        ++rainbow_steps_;
      }
    }
  }

 private:
  static void Led(void* context, longhorn::Console& out, const char* args) {
    auto* self = static_cast<Mvp*>(context);
    if (std::strcmp(args, "auto") == 0) {
      self->rainbow_ = true;
    } else if (std::strcmp(args, "red") == 0 ||
               std::strcmp(args, "green") == 0 ||
               std::strcmp(args, "blue") == 0 ||
               std::strcmp(args, "off") == 0) {
      self->rainbow_ = false;
      self->led_->Set(std::strcmp(args, "red") == 0 ? 0.25f : 0,
                      std::strcmp(args, "green") == 0 ? 0.25f : 0,
                      std::strcmp(args, "blue") == 0 ? 0.25f : 0);
    } else if (*args != '\0') {
      out.Println("usage: /led red|green|blue|off|auto");
      return;
    }
    out.Printf("led=%s rainbow_steps=%lu", self->rainbow_ ? "auto" : "manual",
               static_cast<unsigned long>(self->rainbow_steps_));
  }
  bool EnterDfu() {
    if (!dfu_requested_.exchange(false)) return false;
    enter_dfu_();
    return true;
  }
  static void Dfu(void* context, longhorn::Console&, const char*) {
    static_cast<Mvp*>(context)->dfu_requested_.store(true);
  }
  static bool Byte(void* context, uint8_t byte) {
    auto* self = static_cast<Mvp*>(context);
    const bool matched = self->token_.Feed(byte);
    if (matched) self->dfu_requested_.store(true);
    return matched;
  }
  static void Receive(void* context, const uint8_t* bytes, size_t len) {
    for (size_t i = 0; i < len; ++i) Byte(context, bytes[i]);
  }

  lhal::Clock* clock_;
  longhorn::RgbLed* led_;
  void (*enter_dfu_)();
  longhorn::Shell shell_;
  DfuCommand token_;
  std::atomic<bool> dfu_requested_{false};
  uint32_t last_led_ms_ = 0;
  uint32_t rainbow_steps_ = 0;
  bool rainbow_ = true;
};

}  // namespace vcu
