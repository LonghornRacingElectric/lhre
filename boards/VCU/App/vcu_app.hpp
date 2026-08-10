#pragma once

#include <cstdint>

#include "FreeRTOS.h"
#include "lhal/lhal.hpp"
#include "task.h"

namespace vcu {

// Everything the application needs from the outside world. On the target
// these are LHAL STM32 adapters wired up in main.cpp; in tests and sims they
// are LHAL host fakes. `clock` is required; the rest may be left null until
// the corresponding peripheral is brought up.
struct Peripherals {
  lhal::Clock* clock = nullptr;
  lhal::Gpio* status_led = nullptr;
  lhal::CanBus* can = nullptr;
  lhal::Uart* debug_uart = nullptr;
};

// VCU application logic. LHAL interfaces + raw FreeRTOS API — no ST HAL —
// so it compiles and runs on the host unchanged (the host build links the
// kernel's simulator port; see drivers/freertos).
//
// Two ways to run it:
//   - StartTasks() + vTaskStartScheduler(): one statically-allocated task
//     per periodic activity. Firmware, :vcu_sim, and :vcu_rtos_test do this.
//   - Step() in a plain loop: no scheduler; timing from `clock`. Kept for
//     deterministic unit tests (:vcu_app_test drives a TestClock).
// Both paths call the same underlying logic (SendHeartbeat, LED toggle).
class App {
 public:
  static constexpr uint32_t kBlinkPeriodMs = 500;
  static constexpr uint32_t kHeartbeatPeriodMs = 100;
  static constexpr uint32_t kHeartbeatCanId = 0x100;

  explicit App(const Peripherals& peripherals);

  // Creates the app's FreeRTOS tasks (statically allocated — no heap use).
  // Call once, before vTaskStartScheduler(). The App must outlive the
  // scheduler; on the MCU that means static storage in main(), since the
  // Cortex-M port reclaims main()'s stack when the scheduler starts.
  void StartTasks();

  // One iteration of the scheduler-less main loop. Non-blocking.
  void Step();

  uint32_t heartbeats_sent() const { return heartbeats_sent_; }

 private:
  // In words, and relative to the platform's minimum: 2×128 words on the
  // MCU (headroom for the CAN backend under the heartbeat task), 2×8192 on
  // the host, where the simulator port needs page-sized pthread stacks (see
  // drivers/freertos/host/FreeRTOSConfig.h).
  static constexpr configSTACK_DEPTH_TYPE kTaskStackDepth =
      2 * configMINIMAL_STACK_SIZE;

  static void BlinkTaskEntry(void* self);
  static void HeartbeatTaskEntry(void* self);
  [[noreturn]] void BlinkTaskLoop();
  [[noreturn]] void HeartbeatTaskLoop();

  void SendHeartbeat();

  Peripherals p_;
  bool started_ = false;
  uint32_t last_blink_ms_ = 0;
  uint32_t last_heartbeat_ms_ = 0;
  uint32_t heartbeats_sent_ = 0;

  StaticTask_t blink_tcb_;
  StackType_t blink_stack_[kTaskStackDepth];
  StaticTask_t heartbeat_tcb_;
  StackType_t heartbeat_stack_[kTaskStackDepth];
};

}  // namespace vcu
