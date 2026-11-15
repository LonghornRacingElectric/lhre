#include "dui_app.hpp"

namespace dui {

namespace {

// The four steering switches as UP/DOWN pairs. A switch can only be pushed
// one way, so both lines active at once means a wiring or switch fault.
struct SwitchPair {
  const char* name;
  Input up;
  Input down;
};
constexpr SwitchPair kSwitches[] = {
    {"TOP_L", Input::kTopLeftUp, Input::kTopLeftDown},
    {"TOP_R", Input::kTopRightUp, Input::kTopRightDown},
    {"BOT_L", Input::kBottomLeftUp, Input::kBottomLeftDown},
    {"BOT_R", Input::kBottomRightUp, Input::kBottomRightDown},
};

}  // namespace

App::App(const Peripherals& peripherals)
    : p_(peripherals),
      led_(p_.led_red, p_.led_green, p_.led_blue, p_.led_active_low),
      inputs_(p_.inputs),
      console_mutex_(xSemaphoreCreateMutexStatic(&console_mutex_control_)),
      shell_(p_.console, p_.clock, "DUI", console_mutex_),
      logger_(p_.console, p_.clock, console_mutex_) {
  shell_.AddCommand({"inputs", "driver buttons and steering switches",
                     [](void* context, longhorn::Console& out, const char*) {
                       static_cast<App*>(context)->PrintInputs(out);
                     },
                     this});
  shell_.PrintBanner();
}

void App::StartTasks() {
  // Inputs outrank everything: a late sample stretches the debounce window,
  // a late LED frame or console reply is invisible. The logger drains at the
  // bottom so a slow USB host never delays sampling.
  logger_.StartTask(tskIDLE_PRIORITY + 1);
  xTaskCreateStatic(&App::InputTaskEntry, "inputs", kFormattingStackDepth, this,
                    tskIDLE_PRIORITY + 3, input_stack_, &input_tcb_);
  xTaskCreateStatic(&App::LedTaskEntry, "led", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 2, led_stack_, &led_tcb_);
  xTaskCreateStatic(&App::ShellTaskEntry, "shell", kFormattingStackDepth, this,
                    tskIDLE_PRIORITY + 1, shell_stack_, &shell_tcb_);
}

void App::LedTaskEntry(void* self) { static_cast<App*>(self)->LedTaskLoop(); }

void App::InputTaskEntry(void* self) {
  static_cast<App*>(self)->InputTaskLoop();
}

void App::ShellTaskEntry(void* self) {
  static_cast<App*>(self)->ShellTaskLoop();
}

void App::LedTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    UpdateLed();
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kUpdatePeriodMs));
  }
}

void App::InputTaskLoop() {
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {
    SampleInputs(p_.clock->Millis());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kInputPeriodMs));
  }
}

void App::ShellTaskLoop() {
  for (;;) {
    shell_.Poll();
    vTaskDelay(pdMS_TO_TICKS(kShellPollMs));
  }
}

void App::Step() {
  shell_.Poll();
  logger_.DrainOne(0);

  const uint32_t now = p_.clock->Millis();

  // First iteration: backdate the timers so the lamp test and the first
  // input sample happen immediately instead of one period after boot.
  if (!started_) {
    started_ = true;
    last_update_ms_ = now - kUpdatePeriodMs;
    last_sample_ms_ = now - kInputPeriodMs;
  }

  if (lhal::ElapsedMs(now, last_sample_ms_, kInputPeriodMs)) {
    last_sample_ms_ = now;
    SampleInputs(now);
  }

  if (lhal::ElapsedMs(now, last_update_ms_, kUpdatePeriodMs)) {
    last_update_ms_ = now;
    UpdateLed();
  }
}

void App::UpdateLed() {
  if (elapsed_ms_ < kLampTestMs) {
    led_.Set(Color::kWhite);
  } else {
    const uint32_t phase_ms = (elapsed_ms_ - kLampTestMs) % kRainbowPeriodMs;
    led_.SetRgb(HueToRgb(static_cast<float>(phase_ms) /
                         static_cast<float>(kRainbowPeriodMs)));
  }
  elapsed_ms_ += kUpdatePeriodMs;
  // Fold whole laps back so the counter never wraps into the lamp test.
  if (elapsed_ms_ >= kLampTestMs + kRainbowPeriodMs) {
    elapsed_ms_ -= kRainbowPeriodMs;
  }
  ++updates_;
}

void App::SampleInputs(uint32_t now_ms) {
  const uint16_t changed = inputs_.Sample(now_ms);
  if (changed == 0) {
    return;
  }
  for (size_t i = 0; i < kInputCount; ++i) {
    const auto input = static_cast<Input>(i);
    if ((changed & InputBit(input)) != 0) {
      logger_.Info("%s %s", InputName(input),
                   inputs_.pressed(input) ? "pressed" : "released");
    }
  }
  for (const SwitchPair& sw : kSwitches) {
    const uint16_t pair = InputBit(sw.up) | InputBit(sw.down);
    if ((changed & pair) != 0 && inputs_.pressed(sw.up) &&
        inputs_.pressed(sw.down)) {
      logger_.Error("%s: UP and DOWN both active (switch or wiring fault?)",
                    sw.name);
    }
  }
}

void App::PrintInputs(longhorn::Console& out) {
  out.Printf("BTN1=%d BTN2=%d", inputs_.pressed(Input::kButton1) ? 1 : 0,
             inputs_.pressed(Input::kButton2) ? 1 : 0);
  for (const SwitchPair& sw : kSwitches) {
    const bool up = inputs_.pressed(sw.up);
    const bool down = inputs_.pressed(sw.down);
    out.Printf("%s=%s", sw.name,
               up && down ? "FAULT(both)"
               : up       ? "UP"
               : down     ? "DOWN"
                          : "neutral");
  }
}

}  // namespace dui
