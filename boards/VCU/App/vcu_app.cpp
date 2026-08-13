#include "vcu_app.hpp"

namespace vcu {

using lhre::can::VcuState;
using lhre::can::hvc::HvcPackStatus;
using lhre::can::vcu::VcuStatus;

App::App(const Peripherals& peripherals) : p_(peripherals) {}

void App::StartTasks() {
  // Status outranks blink: losing the CAN status broadcast matters, a late
  // LED toggle doesn't. Both sit above the idle task.
  xTaskCreateStatic(&App::BlinkTaskEntry, "blink", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, blink_stack_, &blink_tcb_);
  xTaskCreateStatic(&App::StatusTaskEntry, "status", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 2, status_stack_, &status_tcb_);
}

void App::BlinkTaskEntry(void* self) {
  static_cast<App*>(self)->BlinkTaskLoop();
}

void App::StatusTaskEntry(void* self) {
  static_cast<App*>(self)->StatusTaskLoop();
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

void App::Step() {
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
