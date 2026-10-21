#include "vcu_app.hpp"

#include "lhre_can_vcu.hpp"

namespace vcu {
namespace msg = lhre::can::vcu;
namespace {
int Send(lhal::CanBus* bus, const lhal::CanFrame& frame) {
  return bus != nullptr && lhal::IsOk(bus->Send(frame)) ? 1 : 0;
}
}  // namespace
// Supervisor

void App::SupervisorTick(uint32_t now) {
  // Like last year's DFU helper, updates work without the rest of the car
  // and do not depend on control-task health. Quiesce outputs before reset.
  if (bootloader_request_.exchange(false) && p_.board != nullptr) {
    bootloader_entered_.store(true);
    WriteDigitalOutputs(p_.outputs, SafeOutputs());
    msg::InverterTorqueCommand stop;
    stop.direction = true;
    Send(p_.critical_can, stop.ToFrame());
    p_.board->EnterBootloader();
    return;
  }
  if (!watchdog_started_) {
    watchdog_started_ = true;
    control_progress_ms_ = now;
    telemetry_progress_ms_ = now;
    if (p_.watchdog != nullptr) {
      p_.watchdog->Start(kWatchdogTimeoutMs);
    }
  }
  const uint32_t control = control_beats_.load();
  const uint32_t telemetry = telemetry_beats_.load();
  if (control != seen_control_beats_) {
    seen_control_beats_ = control;
    control_progress_ms_ = now;
  }
  if (telemetry != seen_telemetry_beats_) {
    seen_telemetry_beats_ = telemetry;
    telemetry_progress_ms_ = now;
  }
  const bool healthy =
      !lhal::ElapsedMs(now, control_progress_ms_, kControlDeadlineMs) &&
      !lhal::ElapsedMs(now, telemetry_progress_ms_, kTelemetryDeadlineMs);
  if (healthy) {
    if (p_.watchdog != nullptr) {
      p_.watchdog->Refresh();
    }
    ++watchdog_refreshes_;
  } else {
    // A critical task stalled: outputs safe now, the IWDG resets us within
    // its timeout.
    WriteDigitalOutputs(p_.outputs, SafeOutputs());
  }
}

}  // namespace vcu
