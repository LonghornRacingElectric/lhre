#include "chg_app.hpp"

namespace chg {

App::App(const Peripherals& peripherals) : p_(peripherals) {
  chg_ui_hooks hooks{};
  hooks.write = Write;
  hooks.get_telemetry = Snapshot;
  if (p_.controller.request_start && p_.controller.request_stop) {
    hooks.request_start = Start;
  }
  if (p_.controller.request_stop) hooks.request_stop = Stop;
  if (p_.controller.acknowledge_fault) hooks.acknowledge_fault = Acknowledge;
  hooks.context = this;
  chg_ui_init(&link_, &hooks);
}

void App::Step() {
  if (!p_.clock || !p_.display_uart) return;
  chg_ui_tick(&link_, p_.clock->Millis());
  for (size_t i = 0; i < kRxBudget; ++i) {
    uint8_t byte;
    if (!lhal::IsOk(p_.display_uart->Read(&byte, 1, 0))) break;
    chg_ui_receive_byte(&link_, byte, p_.clock->Millis());
  }
  chg_ui_tick(&link_, p_.clock->Millis());
}

void App::Write(const uint8_t* data, size_t size, void* context) {
  auto& self = *static_cast<App*>(context);
  if (!self.p_.display_uart ||
      !lhal::IsOk(self.p_.display_uart->Write(data, size, kWriteTimeoutMs))) {
    if (!self.transport_fault_ && self.p_.controller.request_stop) {
      self.p_.controller.request_stop(CHG_STOP_LINK_LOSS,
                                      self.p_.controller.context);
    }
    self.transport_fault_ = true;
  }
}

void App::Snapshot(chg_telemetry* telemetry, void* context) {
  auto& self = *static_cast<App*>(context);
  if (self.p_.controller.get_telemetry) {
    self.p_.controller.get_telemetry(telemetry, self.p_.controller.context);
  }
  if (self.transport_fault_) {
    telemetry->fault_bits |= 1u << 9;
    telemetry->allowed_power_w = 0;
  }
}

uint8_t App::Start(void* context) {
  auto& self = *static_cast<App*>(context);
  if (self.transport_fault_) return CHG_REJECTED;
  return self.p_.controller.request_start(self.p_.controller.context);
}

void App::Stop(uint8_t reason, void* context) {
  auto& self = *static_cast<App*>(context);
  self.p_.controller.request_stop(reason, self.p_.controller.context);
}

void App::Acknowledge(void* context) {
  auto& self = *static_cast<App*>(context);
  self.p_.controller.acknowledge_fault(self.p_.controller.context);
}

}  // namespace chg
