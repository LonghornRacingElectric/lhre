#include "vcu_app.hpp"

#include "status_led.hpp"

namespace vcu {
using lhre::can::EventMode;
App::App(const Peripherals& peripherals, const Params* initial_params)
    : p_(peripherals),
      vcp_mutex_(xSemaphoreCreateMutexStatic(&vcp_mutex_control_)),
      usb_mutex_(xSemaphoreCreateMutexStatic(&usb_mutex_control_)),
      vcp_shell_(p_.debug_uart, p_.clock, "VCU", vcp_mutex_),
      usb_shell_(p_.usb_uart, p_.clock, "VCU", usb_mutex_),
      logger_(p_.debug_uart, p_.clock, vcp_mutex_),
      rainbow_(p_.status_led, p_.clock),
      params_(initial_params != nullptr ? initial_params
                                        : &ParamsFor(kDefaultEventMode)),
      snapshot_mutex_(xSemaphoreCreateMutexStatic(&snapshot_mutex_control_)) {
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
  if (p_.usb_uart == nullptr ||
      !p_.usb_uart->SetRxObserver(&App::UsbReceive, this)) {
    usb_shell_.SetByteHandler(&App::UsbByte, this);
  }
  vcp_shell_.SetByteHandler(&App::VcpByte, this);
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
    app->usb_shell_.Poll();
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
  usb_shell_.Poll();
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

}  // namespace vcu
