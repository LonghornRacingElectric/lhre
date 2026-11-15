#pragma once

#include <cstdint>

#include "FreeRTOS.h"
#include "driver_inputs.hpp"
#include "lhal/lhal.hpp"
#include "longhorn/led.hpp"
#include "longhorn/logger.hpp"
#include "longhorn/shell.hpp"
#include "semphr.h"
#include "task.h"

namespace dui {

// Everything the application needs from the outside world. On the target
// these are LHAL STM32 adapters wired up in Board/main.cpp; in tests and
// sims they are LHAL host fakes. `clock` is required; anything else left
// null is simply skipped.
struct Peripherals {
  lhal::Clock* clock = nullptr;
  // RGB LED D11, one PWM channel per color (TIM2_CH2/CH1/CH3).
  lhal::Pwm* led_red = nullptr;
  lhal::Pwm* led_green = nullptr;
  lhal::Pwm* led_blue = nullptr;
  // D11 is common-anode (hardware is active-low). Tests flip this to read
  // duties plainly.
  bool led_active_low = true;
  // Driver buttons and steering switches, indexed by Input.
  lhal::Gpio* inputs[kInputCount] = {};
  // Debug console: USB CDC on the board (tools/monitor is the laptop side).
  lhal::Uart* console = nullptr;
  // Reboots into the ST ROM bootloader (USB DFU) when tools/dfu sends
  // "update.". Left null in host tests and sims.
  void (*enter_bootloader)() = nullptr;
};

// DUI application logic. LHAL interfaces + raw FreeRTOS only — no ST HAL —
// so it compiles and runs on the host unchanged.
//
// Rev A bring-up:
//   - LED: a white lamp test at power-up, then a smooth rainbow (DUI-003 /
//     DUI-F-001).
//   - Inputs: the ten driver lines, debounced, with every change logged to
//     the console and /inputs printing the current state (DUI-007,
//     DUI-F-002/F-003). CAN forwarding comes next; the debounced state()
//     is what it will send.
//
// Two ways to run it:
//   - StartTasks() + vTaskStartScheduler(): one statically-allocated task
//     per periodic activity. Firmware and :dui_sim do this.
//   - Step() in a plain loop: no scheduler; timing from `clock`. Kept for
//     deterministic unit tests (:dui_app_test drives a TestClock).
// Both paths call the same underlying logic (UpdateLed, SampleInputs).
class App {
 public:
  // All three channels at mid-brightness white for this long after boot.
  static constexpr uint32_t kLampTestMs = 500;
  // LED refresh rate: 100 Hz is smooth to the eye and trivial for the CPU.
  static constexpr uint32_t kUpdatePeriodMs = 10;
  // Input sampling: 1 kHz gives the 5 ms debounce 1 ms resolution.
  static constexpr uint32_t kInputPeriodMs = 1;
  // Shell RX poll. USB CDC buffers 1 KB, so this only sets typing latency.
  static constexpr uint32_t kShellPollMs = 10;

  explicit App(const Peripherals& peripherals);

  // Creates the app's FreeRTOS tasks (statically allocated — no heap use).
  // Call once, before vTaskStartScheduler(). The App must outlive the
  // scheduler; on the MCU that means static storage in main(), since the
  // Cortex-M port reclaims main()'s stack when the scheduler starts.
  void StartTasks();

  // One iteration of the scheduler-less main loop. Non-blocking.
  void Step();

  uint32_t updates() const { return updates_; }
  const DriverInputs& inputs() const { return inputs_; }
  longhorn::Logger& logger() { return logger_; }

 private:
  // Adapts an optional lhal::Pwm channel for longhorn::RgbLed, handling null
  // channels (tests without LEDs) and active-low common-anode inversion.
  class PwmChannel final : public lhal::Pwm {
   public:
    PwmChannel(lhal::Pwm* inner, bool active_low)
        : inner_(inner), active_low_(active_low) {}

    void SetDuty(float duty) override {
      if (inner_ == nullptr) {
        return;
      }
      if (duty < 0.0f) {
        duty = 0.0f;
      } else if (duty > 1.0f) {
        duty = 1.0f;
      }
      inner_->SetDuty(active_low_ ? 1.0f - duty : duty);
    }

   private:
    lhal::Pwm* inner_;
    bool active_low_;
  };
  // In words, relative to the platform's minimum (the host simulator port
  // needs page-sized pthread stacks; see drivers/freertos/host). Tasks that
  // format text need room for the logger's 256-byte line plus vsnprintf.
  static constexpr configSTACK_DEPTH_TYPE kTaskStackDepth =
      2 * configMINIMAL_STACK_SIZE;
  static constexpr configSTACK_DEPTH_TYPE kFormattingStackDepth =
      4 * configMINIMAL_STACK_SIZE;

  static void LedTaskEntry(void* self);
  static void InputTaskEntry(void* self);
  static void ShellTaskEntry(void* self);
  [[noreturn]] void LedTaskLoop();
  [[noreturn]] void InputTaskLoop();
  [[noreturn]] void ShellTaskLoop();

  // Shows the color for the current point in the sequence, then advances
  // it by one update period. Time is counted in updates rather than read
  // from the clock, so the pattern is the same on the host and the MCU.
  void UpdateLed();

  // Samples the inputs and logs every debounced change.
  void SampleInputs(uint32_t now_ms);

  // /inputs command payload.
  void PrintInputs(longhorn::Console& out);

  // Handles non-'/' console lines (e.g. tools/dfu's "update." command).
  static void OnPlainLine(void* context, longhorn::Console& out,
                          const char* line);

  Peripherals p_;
  PwmChannel red_ch_;
  PwmChannel green_ch_;
  PwmChannel blue_ch_;
  longhorn::RgbLed led_;
  DriverInputs inputs_;
  StaticSemaphore_t console_mutex_control_;
  SemaphoreHandle_t console_mutex_ = nullptr;
  longhorn::Shell shell_;
  longhorn::Logger logger_;

  uint32_t elapsed_ms_ = 0;
  uint32_t updates_ = 0;
  bool started_ = false;
  uint32_t last_update_ms_ = 0;
  uint32_t last_sample_ms_ = 0;

  StaticTask_t led_tcb_;
  StackType_t led_stack_[kTaskStackDepth];
  StaticTask_t input_tcb_;
  StackType_t input_stack_[kFormattingStackDepth];
  StaticTask_t shell_tcb_;
  StackType_t shell_stack_[kFormattingStackDepth];
};

}  // namespace dui
