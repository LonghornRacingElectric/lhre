// Host simulation of the DUI: runs the real FreeRTOS tasks against LHAL
// host fakes, "presses" a few buttons on a script, and prints what the USB
// console would show, plus the RGB LED's brightness now and then.
//
//   bazel run --config=local //boards/DUI:dui_sim

#include <cstdio>
#include <cstring>

#include "FreeRTOS.h"
#include "dui_app.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "task.h"

namespace {

using dui::Input;

constexpr uint32_t kSimDurationMs = 2500;
constexpr uint32_t kLedPrintPeriodMs = 500;

struct SimWorld {
  lhal::host::SystemClock clock;
  lhal::host::Pwm red;
  lhal::host::Pwm green;
  lhal::host::Pwm blue;
  lhal::host::Gpio lines[dui::kInputCount];
  lhal::host::Uart console;
};

// What the driver does, and when.
struct Action {
  uint32_t at_ms;
  Input input;
  bool pressed;
  const char* typed;  // console command to send instead, if non-null
};
constexpr Action kScript[] = {
    {600, Input::kButton1, true, nullptr},
    {800, Input::kButton1, false, nullptr},
    {1000, Input::kTopLeftUp, true, nullptr},
    {1300, Input::kButton1, false, "/inputs\r"},
    {1500, Input::kTopLeftUp, false, nullptr},
    {1700, Input::kBottomRightDown, true, nullptr},
    {1702, Input::kBottomRightDown, false, nullptr},  // 2 ms glitch: ignored
};

// LED brightness as the eye sees it: active-low, so lit time is 1 - duty.
int Percent(const lhal::host::Pwm& pwm) {
  return static_cast<int>((1.0f - pwm.duty()) * 100.0f + 0.5f);
}

// Plays the script, echoes console output, and ends the scheduler (which
// returns control to main) when the sim time is up.
void DriverTaskLoop(void* arg) {
  SimWorld& world = *static_cast<SimWorld*>(arg);
  size_t next_action = 0;
  uint32_t next_led_print = 0;
  TickType_t last_wake = xTaskGetTickCount();
  while (world.clock.Millis() < kSimDurationMs) {
    const uint32_t now = world.clock.Millis();
    while (next_action < sizeof(kScript) / sizeof(kScript[0]) &&
           kScript[next_action].at_ms <= now) {
      const Action& a = kScript[next_action++];
      if (a.typed != nullptr) {
        std::printf("            > %s\n", a.typed);
        world.console.InjectRx(reinterpret_cast<const uint8_t*>(a.typed),
                               std::strlen(a.typed));
      } else {
        world.lines[static_cast<size_t>(a.input)].Write(a.pressed);
      }
    }
    for (uint8_t byte : world.console.TakeTx()) {
      if (byte != '\r') {
        std::putchar(byte);
      }
    }
    if (now >= next_led_print) {
      next_led_print += kLedPrintPeriodMs;
      std::printf("[%4u ms] (LED R %3d%% G %3d%% B %3d%%)\n", now,
                  Percent(world.red), Percent(world.green),
                  Percent(world.blue));
    }
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(1));
  }
  vTaskEndScheduler();
}

}  // namespace

int main() {
  static SimWorld world;
  world.console.set_connected(true);  // as if tools/monitor were attached

  dui::Peripherals p;
  p.clock = &world.clock;
  p.led_red = &world.red;
  p.led_green = &world.green;
  p.led_blue = &world.blue;
  for (size_t i = 0; i < dui::kInputCount; ++i) {
    p.inputs[i] = &world.lines[i];
  }
  p.console = &world.console;
  static dui::App app(p);

  std::printf("DUI sim: running for %u ms...\n", kSimDurationMs);
  app.StartTasks();

  static StaticTask_t driver_tcb;
  static StackType_t driver_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(DriverTaskLoop, "driver", configMINIMAL_STACK_SIZE, &world,
                    tskIDLE_PRIORITY + 4, driver_stack, &driver_tcb);

  vTaskStartScheduler();  // returns when the driver task ends the sim

  std::printf("DUI sim: done\n");
  return 0;
}
