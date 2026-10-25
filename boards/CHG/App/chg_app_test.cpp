#include "chg_app.hpp"

#include <cassert>
#include <cstdio>

#include "ChargerLink.h"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"

namespace {

struct Rig {
  lhal::host::TestClock clock;
  lhal::host::Uart uart;
  chg_telemetry telemetry{};
  unsigned starts = 0, stops = 0;
  uint8_t stop_reason = 0;
  chg::App app;
  charger::Link display;

  static chg::Peripherals MakePeripherals(Rig* self, bool actions) {
    chg::Peripherals p;
    p.clock = &self->clock;
    p.display_uart = &self->uart;
    p.controller.get_telemetry = Snapshot;
    p.controller.context = self;
    if (actions) {
      p.controller.request_start = Start;
      p.controller.request_stop = Stop;
    }
    return p;
  }
  explicit Rig(bool actions = true)
      : app(MakePeripherals(this, actions)), display(Send, this) {
    telemetry.state = CHG_READY;
    telemetry.ready_mask = telemetry.required_mask = CHG_REQUIRED_READINESS;
    telemetry.allowed_power_w = 6000;
    telemetry.soc_permille = 500;
    telemetry.soe_permille = 480;
    telemetry.target_soc_permille = 900;
    display.setControlsEnabled(true);
  }
  static void Send(const uint8_t* data, size_t size, void* context) {
    static_cast<Rig*>(context)->uart.InjectRx(data, size);
  }
  static void Snapshot(chg_telemetry* t, void* context) {
    *t = static_cast<Rig*>(context)->telemetry;
  }
  static uint8_t Start(void* context) {
    ++static_cast<Rig*>(context)->starts;
    return CHG_ACCEPTED;
  }
  static void Stop(uint8_t reason, void* context) {
    auto* self = static_cast<Rig*>(context);
    ++self->stops;
    self->stop_reason = reason;
  }
  void Pump() {
    app.Step();
    for (uint8_t byte : uart.TakeTx()) display.feed(byte, clock.Millis());
  }
  void Boot() {
    display.beginSession(42, clock.Millis());
    Pump();
    assert(display.connected(clock.Millis()));
  }
};

void ApplicationUsesLhalAndCorrelatesActions() {
  Rig rig;
  rig.Boot();
  assert(rig.display.sendCommand(charger::Start, 0, rig.clock.Millis()));
  rig.Pump();
  assert(rig.starts == 1);
  assert(rig.display.model().lastAck == charger::Accepted);
  assert(rig.display.sendCommand(charger::Stop, 0, rig.clock.Millis()));
  rig.Pump();
  assert(rig.stops == 1 && rig.stop_reason == CHG_STOP_USER);
}

void MissingControllerHooksNeverStartCharging() {
  Rig rig(false);
  rig.Boot();
  assert(rig.display.sendCommand(charger::Start, 0, rig.clock.Millis()));
  rig.Pump();
  assert(rig.starts == 0);
  assert(rig.display.model().lastAck == charger::Unsupported);
}

void DisplayTimeoutStopsAndNeverRestarts() {
  Rig rig;
  rig.Boot();
  rig.clock.Advance(CHG_UI_TIMEOUT_MS + 1);
  rig.Pump();
  assert(rig.stops == 1 && rig.stop_reason == CHG_STOP_LINK_LOSS);
  rig.display.beginSession(43, rig.clock.Millis());
  rig.Pump();
  assert(rig.starts == 0);
}

void LiveControllerStateStillBlocksAQueuedStart() {
  Rig rig;
  rig.Boot();
  assert(rig.display.sendCommand(charger::Start, 0, rig.clock.Millis()));
  rig.telemetry.fault_bits = charger::FaultInterlock;
  rig.Pump();
  assert(rig.starts == 0);
  assert(rig.display.model().lastAck == charger::Rejected);
}

class BrokenTx final : public lhal::Uart {
 public:
  lhal::Status Write(const uint8_t*, size_t, uint32_t) override {
    return lhal::Status::kError;
  }
  lhal::Status Read(uint8_t* data, size_t size, uint32_t timeout) override {
    return rx.Read(data, size, timeout);
  }
  lhal::Status WriteAsync(const uint8_t*, size_t, lhal::CompletionCallback,
                          void*) override {
    return lhal::Status::kError;
  }
  lhal::Status ReadAsync(uint8_t*, size_t, lhal::CompletionCallback,
                         void*) override {
    return lhal::Status::kError;
  }
  lhal::host::Uart rx;
};

void FailedTransmitLatchesAStop() {
  lhal::host::TestClock clock;
  BrokenTx uart;
  unsigned stops = 0;
  chg::Peripherals p;
  p.clock = &clock;
  p.display_uart = &uart;
  p.controller.context = &stops;
  p.controller.request_stop = [](uint8_t reason, void* context) {
    assert(reason == CHG_STOP_LINK_LOSS);
    ++*static_cast<unsigned*>(context);
  };
  chg::App app(p);
  charger::Frame hello{};
  hello.type = charger::Hello;
  hello.session = 91;
  uint8_t data[82];
  size_t size = charger::encodeFrame(hello, data, sizeof(data));
  uart.rx.InjectRx(data, size);
  app.Step();
  assert(app.transport_fault());
  assert(stops == 1);
}

}  // namespace

int main() {
  ApplicationUsesLhalAndCorrelatesActions();
  MissingControllerHooksNeverStartCharging();
  DisplayTimeoutStopsAndNeverRestarts();
  LiveControllerStateStillBlocksAQueuedStart();
  FailedTransmitLatchesAStop();
  std::puts(
      "PASS: CHG LHAL application, UART actions, timeout, live interlocks, TX "
      "failure");
}
