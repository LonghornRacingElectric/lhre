#pragma once

#include <cstddef>
#include <cstdint>

#include "charger_ui_link.h"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"

namespace chg {

struct Peripherals {
  lhal::Clock* clock = nullptr;
  lhal::Uart* display_uart = nullptr;
  // The real controller supplies coherent telemetry and a latched stop path.
  // Null action hooks keep START unsupported during initial bring-up.
  chg_ui_hooks controller{};
};

class App {
 public:
  static constexpr size_t kRxBudget = 128;
  static constexpr uint32_t kWriteTimeoutMs = 10;

  explicit App(const Peripherals& peripherals);
  App(const App&) = delete;
  App& operator=(const App&) = delete;

  // Call frequently from one communications task; never from interrupt context.
  void Step();
  bool transport_fault() const { return transport_fault_; }

 private:
  static void Write(const uint8_t* data, size_t size, void* context);
  static void Snapshot(chg_telemetry* telemetry, void* context);
  static uint8_t Start(void* context);
  static void Stop(uint8_t reason, void* context);
  static void Acknowledge(void* context);

  Peripherals p_;
  chg_ui_link link_{};
  // A failed write is ambiguous; latch it rather than allowing another START.
  bool transport_fault_ = false;
};

}  // namespace chg
