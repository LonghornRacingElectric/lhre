#include "vcu_app.hpp"

namespace vcu {

App::App(const Peripherals& peripherals) : p_(peripherals) {}

void App::Step() {
  const uint32_t now = p_.clock->Millis();

  // First iteration: backdate the timers so everything fires immediately
  // (heartbeats start at boot, not one period after).
  if (!started_) {
    started_ = true;
    last_blink_ms_ = now - kBlinkPeriodMs;
    last_heartbeat_ms_ = now - kHeartbeatPeriodMs;
  }

  if (p_.status_led != nullptr &&
      lhal::ElapsedMs(now, last_blink_ms_, kBlinkPeriodMs)) {
    last_blink_ms_ = now;
    p_.status_led->Toggle();
  }

  if (p_.can != nullptr &&
      lhal::ElapsedMs(now, last_heartbeat_ms_, kHeartbeatPeriodMs)) {
    last_heartbeat_ms_ = now;
    SendHeartbeat();
  }
}

void App::SendHeartbeat() {
  lhal::CanFrame frame;
  frame.id = kHeartbeatCanId;
  frame.len = 4;
  frame.data[0] = static_cast<uint8_t>(heartbeats_sent_ >> 0);
  frame.data[1] = static_cast<uint8_t>(heartbeats_sent_ >> 8);
  frame.data[2] = static_cast<uint8_t>(heartbeats_sent_ >> 16);
  frame.data[3] = static_cast<uint8_t>(heartbeats_sent_ >> 24);
  if (lhal::IsOk(p_.can->Send(frame))) {
    ++heartbeats_sent_;
  }
}

}  // namespace vcu
