#pragma once

#include <cstdint>

#include "lhal/lhal.hpp"

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

// VCU application logic. Pure LHAL — no ST HAL includes — so it compiles and
// runs on the host unchanged.
class App {
 public:
  static constexpr uint32_t kBlinkPeriodMs = 500;
  static constexpr uint32_t kHeartbeatPeriodMs = 100;
  static constexpr uint32_t kHeartbeatCanId = 0x100;

  explicit App(const Peripherals& peripherals);

  // One iteration of the main loop. Non-blocking.
  void Step();

  uint32_t heartbeats_sent() const { return heartbeats_sent_; }

 private:
  void SendHeartbeat();

  Peripherals p_;
  bool started_ = false;
  uint32_t last_blink_ms_ = 0;
  uint32_t last_heartbeat_ms_ = 0;
  uint32_t heartbeats_sent_ = 0;
};

}  // namespace vcu
