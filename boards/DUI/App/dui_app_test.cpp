// Host unit tests for the DUI application logic, using LHAL host fakes.

#include <gtest/gtest.h>

#include <string>

#include "dui_app.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"

namespace {

using dui::App;
using dui::Input;

struct Leds {
  lhal::host::Pwm red;
  lhal::host::Pwm green;
  lhal::host::Pwm blue;
};

dui::Peripherals MakePeripherals(lhal::Clock* clock, Leds* leds,
                                 bool active_low) {
  dui::Peripherals p;
  p.clock = clock;
  p.led_red = &leds->red;
  p.led_green = &leds->green;
  p.led_blue = &leds->blue;
  p.led_active_low = active_low;
  return p;
}

void ExpectDuty(const Leds& actual, float r, float g, float b) {
  EXPECT_NEAR(actual.red.duty(), r, 1e-3f);
  EXPECT_NEAR(actual.green.duty(), g, 1e-3f);
  EXPECT_NEAR(actual.blue.duty(), b, 1e-3f);
}

// Advances the clock to `t_ms` in update-period ticks, stepping the app at
// each one, so the LED shows the color for time `t_ms` (boot = 0).
void StepUntil(App& app, lhal::host::TestClock& clock, uint32_t t_ms) {
  while (clock.Millis() < t_ms) {
    clock.Advance(App::kUpdatePeriodMs);
    app.Step();
  }
}

TEST(DuiApp, StartsWithBootWhite) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/true));

  // longhorn::RgbLed boots at mid-brightness white (0.5 -> 1 - 0.5 = 0.5).
  ExpectDuty(leds, 0.5f, 0.5f, 0.5f);
}

TEST(DuiApp, LampTestThenRainbow) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/false));

  // Mid-brightness white straight away, for the whole lamp test.
  app.Step();
  ExpectDuty(leds, 0.5f, 0.5f, 0.5f);
  StepUntil(app, clock, App::kLampTestMs - App::kUpdatePeriodMs);
  ExpectDuty(leds, 0.5f, 0.5f, 0.5f);

  // Then longhorn::RgbLed::Rainbow advances from red toward green, reaching
  // green a third of a cycle later.
  const uint32_t third_cycle_ms =
      static_cast<uint32_t>(longhorn::RgbLed::kRainbowCycleS * 1000.0f / 3.0f);
  StepUntil(app, clock, App::kLampTestMs);
  EXPECT_GT(leds.red.duty(), 0.0f);
  EXPECT_GT(leds.green.duty(), 0.0f);
  EXPECT_FLOAT_EQ(leds.blue.duty(), 0.0f);

  StepUntil(app, clock, App::kLampTestMs + third_cycle_ms);
  EXPECT_NEAR(leds.red.duty(), 0.0f, 1e-2f);
  EXPECT_NEAR(leds.green.duty(), 0.5f, 1e-2f);
  EXPECT_GT(leds.blue.duty(), 0.0f);
}

TEST(DuiApp, RainbowLoopsWithoutRepeatingLampTest) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/false));

  const uint32_t cycle_ms =
      static_cast<uint32_t>(longhorn::RgbLed::kRainbowCycleS * 1000.0f);
  app.Step();
  StepUntil(app, clock, App::kLampTestMs + 3 * cycle_ms);
  // Three full cycles later: back near red, not white.
  EXPECT_GT(leds.red.duty(), 0.45f);
  EXPECT_FLOAT_EQ(leds.blue.duty(), 0.0f);
}

TEST(DuiApp, UpdatesAtTheConfiguredRate) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/false));

  app.Step();
  clock.Advance(App::kUpdatePeriodMs - 1);
  app.Step();
  EXPECT_EQ(app.updates(), 1u);
  clock.Advance(1);
  app.Step();
  EXPECT_EQ(app.updates(), 2u);
}

TEST(DuiApp, RunsWithoutLeds) {
  lhal::host::TestClock clock;
  dui::Peripherals p;
  p.clock = &clock;
  App app(p);

  app.Step();
  StepUntil(app, clock, 90);
  EXPECT_EQ(app.updates(), 10u);  // t = 0, 10, ..., 90
}

// A board with every input wired to a fake GPIO and the console on a fake
// UART with a terminal "attached" (the logger stays silent otherwise).
struct ConsoleRig {
  lhal::host::TestClock clock;
  lhal::host::Gpio lines[dui::kInputCount];
  lhal::host::Uart console;
  App app;

  ConsoleRig() : app(MakeConsolePeripherals(this)) {
    console.TakeTx();  // drop the boot banner
  }

  static dui::Peripherals MakeConsolePeripherals(ConsoleRig* rig) {
    rig->console.set_connected(true);
    dui::Peripherals p;
    p.clock = &rig->clock;
    for (size_t i = 0; i < dui::kInputCount; ++i) {
      p.inputs[i] = &rig->lines[i];
    }
    p.console = &rig->console;
    return p;
  }

  lhal::host::Gpio& line(Input input) {
    return lines[static_cast<size_t>(input)];
  }

  // Runs the app for `ms` in 1 ms steps, then flushes queued log lines.
  void Run(uint32_t ms) {
    for (uint32_t i = 0; i < ms; ++i) {
      app.Step();
      clock.Advance(1);
    }
    for (size_t i = 0; i < longhorn::Logger::kQueueLength; ++i) {
      app.logger().DrainOne(0);
    }
  }

  void Type(const std::string& text) {
    console.InjectRx(reinterpret_cast<const uint8_t*>(text.data()),
                     text.size());
  }

  std::string TakeOutput() {
    const auto tx = console.TakeTx();
    return std::string(tx.begin(), tx.end());
  }
};

TEST(DuiConsole, LogsDebouncedPressAndRelease) {
  ConsoleRig rig;
  rig.Run(5);

  rig.line(Input::kButton1).Write(true);
  rig.Run(20);
  EXPECT_NE(rig.TakeOutput().find("BTN1 pressed"), std::string::npos);

  rig.line(Input::kButton1).Write(false);
  rig.Run(20);
  EXPECT_NE(rig.TakeOutput().find("BTN1 released"), std::string::npos);
}

TEST(DuiConsole, IgnoresGlitchesShorterThanDebounce) {
  ConsoleRig rig;
  rig.Run(5);

  rig.line(Input::kBottomLeftDown).Write(true);
  rig.Run(3);
  rig.line(Input::kBottomLeftDown).Write(false);
  rig.Run(30);
  EXPECT_EQ(rig.TakeOutput().find("BOT_L_DOWN"), std::string::npos);
}

TEST(DuiConsole, InputsCommandShowsSwitchPositions) {
  ConsoleRig rig;
  rig.line(Input::kTopLeftUp).Write(true);
  rig.line(Input::kBottomRightDown).Write(true);
  rig.Run(20);
  rig.TakeOutput();

  rig.Type("/inputs\r");
  rig.Run(2);
  const std::string out = rig.TakeOutput();
  EXPECT_NE(out.find("BTN1=0 BTN2=0"), std::string::npos) << out;
  EXPECT_NE(out.find("TOP_L=UP"), std::string::npos) << out;
  EXPECT_NE(out.find("TOP_R=neutral"), std::string::npos) << out;
  EXPECT_NE(out.find("BOT_R=DOWN"), std::string::npos) << out;
}

TEST(DuiConsole, FlagsSwitchActiveBothWays) {
  ConsoleRig rig;
  rig.line(Input::kTopRightUp).Write(true);
  rig.line(Input::kTopRightDown).Write(true);
  rig.Run(20);
  EXPECT_NE(rig.TakeOutput().find("TOP_R: UP and DOWN both active"),
            std::string::npos);
}

TEST(DuiConsole, InputsStillDebouncedWithNoTerminalAttached) {
  ConsoleRig rig;
  rig.console.set_connected(false);
  rig.line(Input::kButton2).Write(true);
  rig.Run(20);
  EXPECT_TRUE(rig.app.inputs().pressed(Input::kButton2));
  EXPECT_TRUE(rig.TakeOutput().empty());  // logger skips when nobody listens
}

TEST(DuiApp, ActiveLowInvertsDuty) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/true));

  app.Step();
  StepUntil(app, clock, App::kLampTestMs);
  // Blue is 0% brightness on the red->green edge -> 100% duty (pin high).
  EXPECT_LT(leds.red.duty(), 1.0f);
  EXPECT_FLOAT_EQ(leds.blue.duty(), 1.0f);
}

TEST(DuiConsole, UpdateCommandTriggersBootloaderCallback) {
  static int calls = 0;
  calls = 0;

  lhal::host::TestClock clock;
  lhal::host::Uart console;
  console.set_connected(true);

  dui::Peripherals p;
  p.clock = &clock;
  p.console = &console;
  p.enter_bootloader = []() { ++calls; };
  App app(p);
  console.TakeTx();

  const std::string bad_cmd = "hello\n";
  console.InjectRx(reinterpret_cast<const uint8_t*>(bad_cmd.data()),
                   bad_cmd.size());
  app.Step();
  EXPECT_EQ(calls, 0);
  const auto tx = console.TakeTx();
  const std::string out(tx.begin(), tx.end());
  EXPECT_NE(out.find("commands start with '/'"), std::string::npos) << out;

  const std::string dfu_cmd = "update.\n";
  console.InjectRx(reinterpret_cast<const uint8_t*>(dfu_cmd.data()),
                   dfu_cmd.size());
  app.Step();
  EXPECT_EQ(calls, 1);
}

}  // namespace
