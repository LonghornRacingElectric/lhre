#include "app/vcu_app.hpp"

#include "app/serial_report.hpp"
#include "hmi/status_led.hpp"
#include "longhorn/rtos_lock.hpp"

namespace vcu {

App::App(const Peripherals& peripherals, const Params* initial_params)
    : p_(peripherals),
      vcp_mutex_(xSemaphoreCreateMutexStatic(&vcp_mutex_control_)),
      usb_mutex_(xSemaphoreCreateMutexStatic(&usb_mutex_control_)),
      vcp_shell_(p_.debug_uart, p_.clock, "Vehicle Control Unit", vcp_mutex_),
      usb_shell_(p_.usb_uart, p_.clock, "Vehicle Control Unit", usb_mutex_),
      logger_(p_.debug_uart, p_.clock, vcp_mutex_),
      rainbow_(p_.status_led, p_.clock),
      params_(initial_params != nullptr ? initial_params
                                        : &ParamsFor(kDefaultEventMode)),
      snapshot_mutex_(xSemaphoreCreateMutexStatic(&snapshot_mutex_control_)) {
  const Rgb boot_color =
      StatusColor(VcuState::kInit, lhre::can::VcuFaultCode::kNone, 0);
  rainbow_.Override(boot_color.r, boot_color.g, boot_color.b);
  // Outputs to their safe state before anything else runs (Board/ already
  // did this at the pin level before constructing the app).
  WriteDigitalOutputs(p_.outputs, SafeOutputs());
  if (p_.audio_dac != nullptr) {
    p_.audio_dac->Stop(kDacMidScale);
  }
  if (p_.board != nullptr) {
    brake_latch_.Restore(p_.board->LoadFaultLatch());
  }
  critical_inbox_.Attach(p_.critical_can);
  data_inbox_.Attach(p_.data_can);
  live_.event_mode = params_->event_mode;
  published_ = live_;

  RegisterShellCommands(vcp_shell_);
  RegisterShellCommands(usb_shell_);
  usb_dfu_.Attach(usb_shell_, p_.usb_uart);
  vcp_dfu_.AttachByteHook(vcp_shell_);
  vcp_shell_.PrintBanner();
  logger_.Info("reset cause: %s", lhal::ToString(p_.reset_cause));
  if (brake_latch_.latched()) {
    logger_.Warning("brake comparator latch restored: dash reset required");
  }
}

void App::StartTasks() {
  // Keep watchdog supervision above control and telemetry.
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  rainbow_.Start();
  xTaskCreateStatic(&App::SupervisorTaskEntry, "supervisor", kSupervisorStack,
                    this, tskIDLE_PRIORITY + 6, supervisor_stack_,
                    &supervisor_tcb_);
  xTaskCreateStatic(&App::ControlTaskEntry, "control", kControlStack, this,
                    tskIDLE_PRIORITY + 5, control_stack_, &control_tcb_);
  xTaskCreateStatic(&App::TelemetryTaskEntry, "telemetry", kTelemetryStack,
                    this, tskIDLE_PRIORITY + 3, telemetry_stack_,
                    &telemetry_tcb_);
  xTaskCreateStatic(&App::VcpShellTaskEntry, "shell_vcp", kShellStack, this,
                    tskIDLE_PRIORITY + 1, vcp_shell_stack_, &vcp_shell_tcb_);
  xTaskCreateStatic(&App::UsbShellTaskEntry, "shell_usb", kShellStack, this,
                    tskIDLE_PRIORITY + 1, usb_shell_stack_, &usb_shell_tcb_);
}

void App::ControlTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->ControlTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kControlPeriodMs));
  }
}

void App::TelemetryTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->TelemetryTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kTelemetryPeriodMs));
  }
}

void App::SupervisorTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    app->SupervisorTick(app->p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kSupervisorPeriodMs));
  }
}

void App::VcpShellTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  for (;;) {
    app->vcp_shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::UsbShellTaskEntry(void* self) {
  auto* app = static_cast<App*>(self);
  for (;;) {
    app->PollUsbConsole();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::Step() {
  const uint32_t now = p_.clock->Millis();
  if (!stepped_) {
    // Backdate so every tick runs on the first call.
    stepped_ = true;
    step_control_ms_ = now - kControlPeriodMs;
    step_telemetry_ms_ = now - kTelemetryPeriodMs;
    step_supervisor_ms_ = now - kSupervisorPeriodMs;
  }
  vcp_shell_.Poll();
  PollUsbConsole();
  rainbow_.Step(now);
  logger_.DrainOne(0);
  if (lhal::ElapsedMs(now, step_control_ms_, kControlPeriodMs)) {
    step_control_ms_ = now;
    ControlTick(now);
  }
  if (lhal::ElapsedMs(now, step_telemetry_ms_, kTelemetryPeriodMs)) {
    step_telemetry_ms_ = now;
    TelemetryTick(now);
  }
  if (lhal::ElapsedMs(now, step_supervisor_ms_, kSupervisorPeriodMs)) {
    step_supervisor_ms_ = now;
    SupervisorTick(now);
  }
}

void App::PollUsbConsole() {
  const bool connected = p_.usb_uart != nullptr && p_.usb_uart->connected();
  const uint32_t now = p_.clock->Millis();
  constexpr uint32_t kStatusPeriodMs = 1000;
  if (connected && !usb_console_connected_) {
    usb_shell_.PrintBanner();
    auto& out = usb_shell_.console();
    out.Printf("reset=%s; /help for commands", lhal::ToString(p_.reset_cause));
    usb_stream_last_ms_ = now - kStatusPeriodMs;
  }
  usb_console_connected_ = connected;
  usb_shell_.Poll();
  if (!connected ||
      !lhal::ElapsedMs(now, usb_stream_last_ms_, kStatusPeriodMs)) {
    return;
  }
  usb_stream_last_ms_ = now;
  const ControlSnapshot s = snapshot();
  if constexpr (kDetailedSerialReport) {
    SerialHealth health;
    health.uptime_ms = p_.clock->Millis();
    if (p_.critical_can != nullptr) health.critical = p_.critical_can->stats();
    if (p_.data_can != nullptr) health.data = p_.data_can->stats();
    health.critical_inbox_drops = critical_inbox_.dropped();
    health.data_inbox_drops = data_inbox_.dropped();
    health.control_beats = control_beats_.load();
    health.telemetry_beats = telemetry_beats_.load();
    PrintSerialReport(usb_shell_.console(), s, health, p_.reset_cause);
  } else {
    PrintCompactSerialReport(usb_shell_.console(), s);
  }
}

void App::Publish() {
  // Never block the control loop: skip a publish if a reader holds it.
  longhorn::SchedulerLock lock(snapshot_mutex_, 0);
  if (lock.locked()) {
    published_ = live_;
  }
}

ControlSnapshot App::snapshot() {
  longhorn::SchedulerLock lock(snapshot_mutex_, pdMS_TO_TICKS(5));
  return lock.locked() ? published_ : ControlSnapshot{};
}

}  // namespace vcu
