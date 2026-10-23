#include "vcu_app.hpp"

#include <algorithm>

#include "longhorn/console.hpp"
#include "longhorn/logger.hpp"

namespace vcu {

using lhre::can::VcuState;
using lhre::can::hvc::HvcPackStatus;
using lhre::can::vcu::VcuStatus;

namespace {

const char* StateName(VcuState state) {
  switch (state) {
    case VcuState::kInit:
      return "init";
    case VcuState::kIdle:
      return "idle";
    case VcuState::kDrive:
      return "drive";
    case VcuState::kFault:
      return "fault";
  }
  return "?";
}

}  // namespace

App::App(const Peripherals& peripherals, bool traction_control_enabled)
    : p_(peripherals),
      uart_mutex_(xSemaphoreCreateMutexStatic(&uart_mutex_control_)),
      shell_(p_.debug_uart, p_.clock, "VCU", uart_mutex_),
      logger_(p_.debug_uart, p_.clock, uart_mutex_),
      traction_control_enabled_(traction_control_enabled) {
  shell_.AddCommand({"state", "VCU state, faults, CAN counters",
                     [](void* context, longhorn::Console& out, const char*) {
                       static_cast<App*>(context)->PrintState(out);
                     },
                     this});
  shell_.PrintBanner();
}

void App::StartTasks() {
  // Control outranks status: a late torque command matters more than a
  // late broadcast. Status outranks blink: losing the CAN status broadcast
  // matters, a late LED toggle doesn't. The shell ties blink: a slow console
  // response is as harmless as a late toggle. All sit above the idle task.
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  xTaskCreateStatic(&App::BlinkTaskEntry, "blink", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, blink_stack_, &blink_tcb_);
  xTaskCreateStatic(&App::ControlTaskEntry, "control", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 3, control_stack_, &control_tcb_);
  xTaskCreateStatic(&App::StatusTaskEntry, "status", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 2, status_stack_, &status_tcb_);
  xTaskCreateStatic(&App::ShellTaskEntry, "shell", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, shell_stack_, &shell_tcb_);
}

void App::BlinkTaskEntry(void* self) {
  static_cast<App*>(self)->BlinkTaskLoop();
}

void App::ControlTaskEntry(void* self) {
  static_cast<App*>(self)->ControlTaskLoop();
}

void App::StatusTaskEntry(void* self) {
  static_cast<App*>(self)->StatusTaskLoop();
}

void App::ShellTaskEntry(void* self) {
  static_cast<App*>(self)->ShellTaskLoop();
}

void App::BlinkTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    if (p_.status_led != nullptr) {
      p_.status_led->Toggle();
    }
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kBlinkPeriodMs));
  }
}

void App::ControlTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    // vTaskDelayUntil holds the period exactly; a step measured with
    // Millis() would carry up to 1 ms of quantization.
    ControlStep(kControlPeriodMs / 1000.0f);
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kControlPeriodMs));
  }
}

void App::StatusTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    if (p_.can != nullptr) {
      ProcessCanRx();
      SendStatus();
    }
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kStatusPeriodMs));
  }
}

void App::ShellTaskLoop() {
  for (;;) {
    shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::PrintState(longhorn::Console& out) {
  out.Printf("state=%s overtemp_latched=%d statuses_sent=%lu",
             StateName(state_), overtemp_latched_ ? 1 : 0,
             static_cast<unsigned long>(statuses_sent_));
  if (pack_status_seen_) {
    out.Printf("pack: coolant %d degC", pack_status_.coolant_temp);
  } else {
    out.Println("pack: no HvcPackStatus seen yet");
  }
}

void App::Step() {
  shell_.Poll();
  logger_.DrainOne(0);

  const uint32_t now = p_.clock->Millis();

  // First iteration: backdate the timers so everything fires immediately
  // (status broadcasts start at boot, not one period after).
  if (!started_) {
    started_ = true;
    last_blink_ms_ = now - kBlinkPeriodMs;
    last_status_ms_ = now - kStatusPeriodMs;
    last_control_ms_ = now - kControlPeriodMs;
  }

  if (p_.can != nullptr) {
    ProcessCanRx();
  }

  // Before the status, so a broadcast carries this step's command. The
  // step is measured: Step() may be called slower than kControlPeriodMs.
  if (lhal::ElapsedMs(now, last_control_ms_, kControlPeriodMs)) {
    ControlStep((now - last_control_ms_) / 1000.0f);
    last_control_ms_ = now;
  }

  if (p_.status_led != nullptr &&
      lhal::ElapsedMs(now, last_blink_ms_, kBlinkPeriodMs)) {
    last_blink_ms_ = now;
    p_.status_led->Toggle();
    logger_.Info("Ticked!");
    logger_.DrainOne(0);
  }

  if (p_.can != nullptr &&
      lhal::ElapsedMs(now, last_status_ms_, kStatusPeriodMs)) {
    last_status_ms_ = now;
    SendStatus();
  }
}

void App::ProcessCanRx() {
  lhal::CanFrame frame;
  while (p_.can->Receive(&frame)) {
    if (HvcPackStatus::Matches(frame.id) && frame.len >= HvcPackStatus::kDlc) {
      pack_status_ = HvcPackStatus::FromFrame(frame);
      pack_status_seen_ = true;
      if (pack_status_.coolant_temp >= kCoolantOvertempDegC) {
        if (!overtemp_latched_) {
          logger_.Error("Coolant overtemp! %d degC >= %d degC",
                        pack_status_.coolant_temp, kCoolantOvertempDegC);
          logger_.DrainOne(0);
        }
        overtemp_latched_ = true;  // faults latch until reset
      }
    }
    // Other IDs: nothing subscribed yet; drop them.
  }
  state_ = overtemp_latched_ ? VcuState::kFault : VcuState::kIdle;
}

void App::SetDriveInputs(float accel_pedal, float motor_speed_rad_s) {
  accel_pedal_ = accel_pedal;
  motor_speed_rad_s_ = motor_speed_rad_s;
}

void App::ControlStep(float dt_s) {
  const float demand_nm =
      std::clamp(accel_pedal_, 0.0f, 1.0f) * kMaxDriveTorqueNm;
  torque_command_nm_ =
      traction_control_enabled_
          ? traction_control_.Apply(demand_nm, motor_speed_rad_s_, dt_s)
          : demand_nm;
}

void App::SendStatus() {
  VcuStatus status;
  status.state = state_;
  status.faults_overtemp = overtemp_latched_;
  status.set_torque_request(torque_command_nm_.load());
  status.speed_raw = 0;
  if (lhal::IsOk(p_.can->Send(status.ToFrame()))) {
    ++statuses_sent_;
  }
}

}  // namespace vcu
