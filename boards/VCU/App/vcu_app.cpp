#include "vcu_app.hpp"
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

App::App(const Peripherals& peripherals)
    : p_(peripherals),
      uart_mutex_(xSemaphoreCreateMutexStatic(&uart_mutex_control_)),
      shell_(p_.debug_uart, p_.clock, "VCU", uart_mutex_),
      logger_(p_.debug_uart, p_.clock, uart_mutex_) {
  shell_.AddCommand({"state", "VCU state, faults, CAN counters",
                     [](void* context, longhorn::Console& out, const char*) {
                       static_cast<App*>(context)->PrintState(out);
                     },
                     this});
  shell_.PrintBanner();
}

void App::StartTasks() {
  // Status outranks blink: losing the CAN status broadcast matters, a late
  // LED toggle doesn't. The shell ties blink: a slow console response is
  // as harmless as a late toggle. All sit above the idle task.
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  xTaskCreateStatic(&App::BlinkTaskEntry, "blink", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, blink_stack_, &blink_tcb_);
  xTaskCreateStatic(&App::StatusTaskEntry, "status", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 2, status_stack_, &status_tcb_);
  xTaskCreateStatic(&App::ShellTaskEntry, "shell", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, shell_stack_, &shell_tcb_);
}

void App::BlinkTaskEntry(void* self) {
  static_cast<App*>(self)->BlinkTaskLoop();
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
      logger_.Info("Ticked!");
    }
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kBlinkPeriodMs));
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
  }

  if (p_.can != nullptr) {
    ProcessCanRx();
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

void App::SendStatus() {
  VcuStatus status;
  status.state = state_;
  status.faults_overtemp = overtemp_latched_;
  status.set_torque_request(0.0f);  // no pedal input wired up yet
  status.speed_raw = 0;
  if (lhal::IsOk(p_.can->Send(status.ToFrame()))) {
    ++statuses_sent_;
  }
}

}  // namespace vcu
