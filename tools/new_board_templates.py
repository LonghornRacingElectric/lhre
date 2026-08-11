"""Starter-file templates for new_board.py.

firmware_project synthesizes a board's targets from file names — App/**/*.cpp
becomes the {name}_app library, App/*_test.cpp become host cc_tests,
App/*_sim.cpp become host sims, and Board/ holds the firmware entry point —
but the files themselves are hand-written. These templates give a new board
a compiling, testing, blinking starting point for each of them, shaped like
boards/VCU (the fuller reference: CAN, UART, RTOS test) but cut down to the
one peripheral every board has, a status LED.

Kept as Python strings (not data files) so new_board.py needs no runfiles
plumbing. render() is the only entry point.
"""


def render(name, board, freertos):
    """Returns {board-relative path: content} for the starter files."""
    files = {
        f"App/{name}_app.hpp": _app_hpp(name, board, freertos),
        f"App/{name}_app.cpp": _app_cpp(name, freertos),
        f"App/{name}_app_test.cpp": _app_test_cpp(name, board),
        f"App/{name}_sim.cpp": _sim_cpp(name, board, freertos),
        "Board/main.cpp": _main_cpp(name, board, freertos),
    }
    return files


def _app_hpp(name, board, freertos):
    rtos_includes = '#include "FreeRTOS.h"\n#include "task.h"\n\n' if freertos else ""
    rtos_doc = (
        """//
// Two ways to run it:
//   - StartTasks() + vTaskStartScheduler(): one statically-allocated task
//     per periodic activity. Firmware and :{name}_sim do this.
//   - Step() in a plain loop: no scheduler; timing from `clock`. Kept for
//     deterministic unit tests (:{name}_app_test drives a TestClock).
// Both paths call the same underlying logic.""".format(name=name)
        if freertos
        else """//
// Runs as Step() in a plain loop — firmware, :{name}_sim, and
// :{name}_app_test all drive the same function; only the clock differs.""".format(name=name)
    )
    rtos_members = (
        """
  // Creates the app's FreeRTOS tasks (statically allocated — no heap use).
  // Call once, before vTaskStartScheduler(). The App must outlive the
  // scheduler; on the MCU that means static storage in main(), since the
  // Cortex-M port reclaims main()'s stack when the scheduler starts.
  void StartTasks();
"""
        if freertos
        else ""
    )
    rtos_privates = (
        """
  // In words, and relative to the platform's minimum (the host simulator
  // port needs page-sized pthread stacks; see
  // drivers/freertos/host/FreeRTOSConfig.h).
  static constexpr configSTACK_DEPTH_TYPE kTaskStackDepth =
      2 * configMINIMAL_STACK_SIZE;

  static void BlinkTaskEntry(void* self);
  [[noreturn]] void BlinkTaskLoop();
"""
        if freertos
        else ""
    )
    rtos_fields = (
        """
  StaticTask_t blink_tcb_;
  StackType_t blink_stack_[kTaskStackDepth];"""
        if freertos
        else ""
    )
    return """#pragma once

#include <cstdint>

{rtos_includes}#include "lhal/lhal.hpp"

namespace {name} {{

// Everything the application needs from the outside world. On the target
// these are LHAL STM32 adapters wired up in Board/main.cpp; in tests and
// sims they are LHAL host fakes. `clock` is required; grow this struct as
// peripherals are brought up (boards/VCU shows CAN and UART wiring).
struct Peripherals {{
  lhal::Clock* clock = nullptr;
  lhal::Gpio* status_led = nullptr;
}};

// {board} application logic. LHAL interfaces only — no ST HAL — so it
// compiles and runs on the host unchanged.
{rtos_doc}
class App {{
 public:
  static constexpr uint32_t kBlinkPeriodMs = 500;

  explicit App(const Peripherals& peripherals);
{rtos_members}
  // One iteration of the scheduler-less main loop. Non-blocking.
  void Step();

  uint32_t blinks() const {{ return blinks_; }}

 private:
{rtos_privates}  void Blink();

  Peripherals p_;
  bool started_ = false;
  uint32_t last_blink_ms_ = 0;
  uint32_t blinks_ = 0;{rtos_fields}
}};

}}  // namespace {name}
""".format(
        name=name,
        board=board,
        rtos_includes=rtos_includes,
        rtos_doc=rtos_doc,
        rtos_members=rtos_members,
        rtos_privates=rtos_privates,
        rtos_fields=rtos_fields,
    )


def _app_cpp(name, freertos):
    rtos_impl = (
        """
void App::StartTasks() {{
  xTaskCreateStatic(&App::BlinkTaskEntry, "blink", kTaskStackDepth, this,
                    tskIDLE_PRIORITY + 1, blink_stack_, &blink_tcb_);
}}

void App::BlinkTaskEntry(void* self) {{
  static_cast<App*>(self)->BlinkTaskLoop();
}}

void App::BlinkTaskLoop() {{
  TickType_t last_wake = xTaskGetTickCount();
  for (;;) {{
    Blink();
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kBlinkPeriodMs));
  }}
}}
"""
        if freertos
        else ""
    )
    return """#include "{name}_app.hpp"

namespace {name} {{

App::App(const Peripherals& peripherals) : p_(peripherals) {{}}
{rtos_impl}
void App::Step() {{
  const uint32_t now = p_.clock->Millis();

  // First iteration: backdate the timer so the first blink fires
  // immediately instead of one period after boot.
  if (!started_) {{
    started_ = true;
    last_blink_ms_ = now - kBlinkPeriodMs;
  }}

  if (lhal::ElapsedMs(now, last_blink_ms_, kBlinkPeriodMs)) {{
    last_blink_ms_ = now;
    Blink();
  }}
}}

void App::Blink() {{
  if (p_.status_led != nullptr) {{
    p_.status_led->Toggle();
    ++blinks_;
  }}
}}

}}  // namespace {name}
""".format(name=name, rtos_impl=rtos_impl)


def _app_test_cpp(name, board):
    suite = board.capitalize() + "App"
    return """// Host unit tests for the {board} application logic, using LHAL host fakes.

#include <gtest/gtest.h>

#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "{name}_app.hpp"

namespace {{

TEST({suite}, BlinksStatusLed) {{
  lhal::host::TestClock clock;
  lhal::host::Gpio led;

  {name}::Peripherals p;
  p.clock = &clock;
  p.status_led = &led;
  {name}::App app(p);

  // Simulate two seconds in 10 ms main-loop ticks.
  for (int i = 0; i < 200; ++i) {{
    app.Step();
    clock.Advance(10);
  }}
  app.Step();

  // 500 ms period over 2 s: toggles at t=0, 500, 1000, 1500, 2000 —
  // five blinks, an odd count, so the LED ends up on.
  EXPECT_EQ(app.blinks(), 5u);
  EXPECT_TRUE(led.Read());
}}

TEST({suite}, RunsWithoutOptionalPeripherals) {{
  lhal::host::TestClock clock;
  {name}::Peripherals p;
  p.clock = &clock;
  {name}::App app(p);

  for (int i = 0; i < 100; ++i) {{
    app.Step();
    clock.Advance(10);
  }}
  EXPECT_EQ(app.blinks(), 0u);
}}

}}  // namespace
""".format(name=name, board=board, suite=suite)


def _sim_cpp(name, board, freertos):
    if freertos:
        body = """#include <cstdio>

#include "FreeRTOS.h"
#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "task.h"
#include "{name}_app.hpp"

namespace {{

constexpr uint32_t kSimDurationMs = 3000;

struct SimWorld {{
  lhal::host::SystemClock clock;
  lhal::host::Gpio led;
}};

// Watches the LED, prints changes, and ends the scheduler (which returns
// control to main) when the sim time is up.
void ObserverTaskLoop(void* arg) {{
  SimWorld& world = *static_cast<SimWorld*>(arg);
  bool last_led = world.led.Read();

  while (world.clock.Millis() < kSimDurationMs) {{
    if (world.led.Read() != last_led) {{
      last_led = world.led.Read();
      std::printf("[%4u ms] LED %s\\n", world.clock.Millis(),
                  last_led ? "on" : "off");
    }}
    vTaskDelay(pdMS_TO_TICKS(1));
  }}
  vTaskEndScheduler();
}}

}}  // namespace

int main() {{
  static SimWorld world;

  {name}::Peripherals p;
  p.clock = &world.clock;
  p.status_led = &world.led;
  static {name}::App app(p);

  std::printf("{board} sim: running for %u ms...\\n", kSimDurationMs);
  app.StartTasks();

  static StaticTask_t observer_tcb;
  static StackType_t observer_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(ObserverTaskLoop, "observer", configMINIMAL_STACK_SIZE,
                    &world, tskIDLE_PRIORITY + 2, observer_stack,
                    &observer_tcb);

  vTaskStartScheduler();  // returns when the observer task ends the sim

  std::printf("{board} sim: done (%u blinks)\\n", app.blinks());
  return 0;
}}
"""
    else:
        body = """#include <cstdio>

#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "{name}_app.hpp"

namespace {{
constexpr uint32_t kSimDurationMs = 3000;
}}  // namespace

int main() {{
  lhal::host::SystemClock clock;
  lhal::host::Gpio led;

  {name}::Peripherals p;
  p.clock = &clock;
  p.status_led = &led;
  {name}::App app(p);

  std::printf("{board} sim: running for %u ms...\\n", kSimDurationMs);
  bool last_led = led.Read();
  while (clock.Millis() < kSimDurationMs) {{
    app.Step();
    if (led.Read() != last_led) {{
      last_led = led.Read();
      std::printf("[%4u ms] LED %s\\n", clock.Millis(), last_led ? "on" : "off");
    }}
    clock.DelayMs(1);
  }}

  std::printf("{board} sim: done (%u blinks)\\n", app.blinks());
  return 0;
}}
"""
    header = """// Host simulation of the {board}: runs the app against LHAL host fakes and
// prints what an observer would see.
//
//   bazel run --config=local //boards/{board}:{name}_sim

"""
    return (header + body).format(name=name, board=board)


def _main_cpp(name, board, freertos):
    rtos_includes = '#include "FreeRTOS.h"\n#include "task.h"\n' if freertos else ""
    if freertos:
        run = """  // Everything the tasks touch must be static: the Cortex-M port reclaims
  // main()'s stack for interrupts when the scheduler starts.
  static lhal::stm32::Clock clock;
  static lhal::stm32::Gpio status_led(kStatusLedPort, kStatusLedPin);

  {name}::Peripherals peripherals;
  peripherals.clock = &clock;
  peripherals.status_led = &status_led;

  static {name}::App app(peripherals);
  app.StartTasks();
  vTaskStartScheduler();

  Error_Handler();  // the scheduler only returns if it failed to start"""
    else:
        run = """  static lhal::stm32::Clock clock;
  static lhal::stm32::Gpio status_led(kStatusLedPort, kStatusLedPin);

  {name}::Peripherals peripherals;
  peripherals.clock = &clock;
  peripherals.status_led = &status_led;

  static {name}::App app(peripherals);
  for (;;) {{
    app.Step();
  }}"""
    return """// {board} firmware entry point — hand-written, not CubeMX-generated.
// Everything under Core/ is CubeMX-owned and safe to regenerate; everything
// under Board/ is ours and CubeMX never touches it. Application logic lives
// in App/{name}_app.* against LHAL interfaces only, so it also runs on the
// host (see :{name}_app_test and :{name}_sim). boards/VCU/Board/main.cpp
// shows fuller bring-up: debug UART, CAN, boot banner.

#include "main.h"

{rtos_includes}#include "lhal/stm32/gpio.hpp"
#include "lhal/stm32/init.hpp"
#include "lhal/stm32/system.hpp"
#include "{name}_app.hpp"

namespace {{

// TODO({name}): point this at the real status LED once the board pinout is
// final. PA5 is the usual Nucleo dev-board LED.
GPIO_TypeDef* const kStatusLedPort = GPIOA;
constexpr uint16_t kStatusLedPin = GPIO_PIN_5;

// Pin mux for peripherals not yet in the .ioc is configured at the ST HAL
// level here; once a peripheral is added in CubeMX, its MX_*_Init() from
// Core/ takes over.
void ConfigureGpio() {{
  __HAL_RCC_GPIOA_CLK_ENABLE();

  GPIO_InitTypeDef init = {{}};
  init.Pin = kStatusLedPin;
  init.Mode = GPIO_MODE_OUTPUT_PP;
  init.Pull = GPIO_NOPULL;
  init.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(kStatusLedPort, &init);
}}

}}  // namespace

int main() {{
  lhal::stm32::InitCore();  // HAL_Init + CubeMX-generated SystemClock_Config
  ConfigureGpio();

{run}
}}

// Error_Handler() and assert_failed() come from the CubeMX-generated
// Core/Src/main.c; customize them there inside USER CODE sections.
""".format(name=name, board=board, rtos_includes=rtos_includes, run=run.format(name=name))
