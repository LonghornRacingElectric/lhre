#pragma once

#include <cstdint>

#include "FreeRTOS.h"
#include "lhal/lhal.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/logger.hpp"
#include "longhorn/shell.hpp"
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
// CAN messages come from the generated spec library (lib/codegen/cpp):
// the status task broadcasts lhre::can::vcu::VcuStatus, and every received
// frame is decoded through the generated Matches()/FromFrame() pairs —
// no hand-packed frames, no magic IDs.
//
// Two ways to run it:
//   - StartTasks() + vTaskStartScheduler(): one statically-allocated task
//     per periodic activity. Firmware, :vcu_sim, and :vcu_rtos_test do this.
//   - Step() in a plain loop: no scheduler; timing from `clock`. Kept for
//     deterministic unit tests (:vcu_app_test drives a TestClock).
// Both paths call the same underlying logic (ProcessCanRx, SendStatus,
// LED toggle).
class App {
 public:
  static constexpr uint32_t kBlinkPeriodMs = 100;
  static constexpr uint32_t kStatusPeriodMs = 100;
  // Shell RX poll period. The UART buffers only a handful of bytes between
  // polls (RX FIFO + RDR), so this must stay well under the time a burst
  // takes to overflow them; tools/monitor paces its probe to match.
  static constexpr uint32_t kShellPollMs = 2;
  // Latch an overtemp fault when the HVC reports coolant at or above this.
  static constexpr int8_t kCoolantOvertempDegC = 60;

  explicit App(const Peripherals& peripherals);

  // Creates the app's FreeRTOS tasks (statically allocated — no heap use).
  // Call once, before vTaskStartScheduler(). The App must outlive the
  // scheduler; on the MCU that means static storage in main(), since the
  // Cortex-M port reclaims main()'s stack when the scheduler starts.
  void StartTasks();

  // One iteration of the scheduler-less main loop. Non-blocking.
  void Step();

  uint32_t statuses_sent() const { return statuses_sent_; }
  // Last accumulator state received from the HVC; meaningful only once
  // pack_status_seen() is true.
  bool pack_status_seen() const { return pack_status_seen_; }
  const lhre::can::hvc::HvcPackStatus& pack_status() const {
    return pack_status_;
  }
  longhorn::Logger& logger() { return logger_; }
  lhre::can::VcuState state() const { return state_; }

 private:
  // In words, and relative to the platform's minimum: 2×128 words on the
  // MCU (headroom for the CAN backend under the status task), 2×8192 on
  // the host, where the simulator port needs page-sized pthread stacks (see
  // drivers/freertos/host/FreeRTOSConfig.h).
  static constexpr configSTACK_DEPTH_TYPE kTaskStackDepth =
      2 * configMINIMAL_STACK_SIZE;

  static void BlinkTaskEntry(void* self);
  static void StatusTaskEntry(void* self);
  static void ShellTaskEntry(void* self);
  [[noreturn]] void BlinkTaskLoop();
  [[noreturn]] void StatusTaskLoop();
  [[noreturn]] void ShellTaskLoop();

  // /state command payload: mode, faults, CAN view. Split out so tests can
  // exercise it through the shell like the monitor would.
  void PrintState(longhorn::Console& out);

  // Drains the CAN RX queue into the app's view of the world.
  void ProcessCanRx();
  void SendStatus();

  Peripherals p_;
  StaticSemaphore_t uart_mutex_control_;
  SemaphoreHandle_t uart_mutex_ = nullptr;
  // Debug shell over debug_uart: /help /version /uptime plus /state below.
  // Polled by its own low-priority task (or Step() when scheduler-less).
  longhorn::Shell shell_;
  // Non-blocking, thread-safe logger over debug_uart. Stamped with [<ms>]
  // [LEVEL] and drained by a low-priority task (or Step() when scheduler-less).
  longhorn::Logger logger_;
  bool started_ = false;
  uint32_t last_blink_ms_ = 0;
  uint32_t last_status_ms_ = 0;
  uint32_t statuses_sent_ = 0;

  lhre::can::VcuState state_ = lhre::can::VcuState::kIdle;
  lhre::can::hvc::HvcPackStatus pack_status_;
  bool pack_status_seen_ = false;
  bool overtemp_latched_ = false;

  StaticTask_t blink_tcb_;
  StackType_t blink_stack_[kTaskStackDepth];
  StaticTask_t status_tcb_;
  StackType_t status_stack_[kTaskStackDepth];
  StaticTask_t shell_tcb_;
  StackType_t shell_stack_[kTaskStackDepth];
};

}  // namespace vcu
