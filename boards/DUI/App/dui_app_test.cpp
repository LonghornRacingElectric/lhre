// Host unit tests for the DUI application logic, using LHAL host fakes.

#include <gtest/gtest.h>

#include <string>

#include "dui_app.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "rgb_led.hpp"

namespace {

using dui::App;
using dui::Color;
using dui::Input;
using dui::Rgb;

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

void ExpectRgb(const Rgb& actual, float r, float g, float b) {
  EXPECT_NEAR(actual.red, r, 1e-4f);
  EXPECT_NEAR(actual.green, g, 1e-4f);
  EXPECT_NEAR(actual.blue, b, 1e-4f);
}

// Advances the clock to `t_ms` in update-period ticks, stepping the app at
// each one, so the LED shows the color for time `t_ms` (boot = 0).
void StepUntil(App& app, lhal::host::TestClock& clock, uint32_t t_ms) {
  while (clock.Millis() < t_ms) {
    clock.Advance(App::kUpdatePeriodMs);
    app.Step();
  }
}

TEST(HueToRgb, HitsTheSixWheelColors) {
  ExpectRgb(dui::HueToRgb(0.0f), 1, 0, 0);         // red
  ExpectRgb(dui::HueToRgb(1.0f / 6.0f), 1, 1, 0);  // yellow
  ExpectRgb(dui::HueToRgb(2.0f / 6.0f), 0, 1, 0);  // green
  ExpectRgb(dui::HueToRgb(3.0f / 6.0f), 0, 1, 1);  // cyan
  ExpectRgb(dui::HueToRgb(4.0f / 6.0f), 0, 0, 1);  // blue
  ExpectRgb(dui::HueToRgb(5.0f / 6.0f), 1, 0, 1);  // magenta
}

TEST(HueToRgb, BlendsBetweenColorsAndWraps) {
  ExpectRgb(dui::HueToRgb(0.5f / 6.0f), 1, 0.5f, 0);  // halfway to yellow
  ExpectRgb(dui::HueToRgb(1.0f), 1, 0, 0);            // full lap = red
  ExpectRgb(dui::HueToRgb(1.25f), 0.5f, 1, 0);        // wraps like 0.25
}

TEST(DuiApp, StartsWithLedOff) {
  // Common-anode: "off" is 100% duty (pin held high) on every channel.
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/true));

  EXPECT_FLOAT_EQ(leds.red.duty(), 1.0f);
  EXPECT_FLOAT_EQ(leds.green.duty(), 1.0f);
  EXPECT_FLOAT_EQ(leds.blue.duty(), 1.0f);
}

TEST(DuiApp, LampTestThenRainbow) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/false));

  // White straight away, for the whole lamp test.
  app.Step();
  ExpectRgb(app.rgb(), 1, 1, 1);
  StepUntil(app, clock, App::kLampTestMs - App::kUpdatePeriodMs);
  ExpectRgb(app.rgb(), 1, 1, 1);

  // Then the wheel: red, and a sixth of a lap later, yellow.
  StepUntil(app, clock, App::kLampTestMs);
  ExpectRgb(app.rgb(), 1, 0, 0);
  StepUntil(app, clock, App::kLampTestMs + App::kRainbowPeriodMs / 6);
  ExpectRgb(app.rgb(), 1, 1, 0);
}

TEST(DuiApp, RainbowLoopsWithoutRepeatingLampTest) {
  lhal::host::TestClock clock;
  Leds leds;
  App app(MakePeripherals(&clock, &leds, /*active_low=*/false));

  app.Step();
  StepUntil(app, clock, App::kLampTestMs + 3 * App::kRainbowPeriodMs);
  ExpectRgb(app.rgb(), 1, 0, 0);  // three laps later: red, not white
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

TEST(RgbLed, ActiveLowInvertsDuty) {
  Leds leds;
  dui::RgbLed led(&leds.red, &leds.green, &leds.blue, /*active_low=*/true);

  led.SetRgb({1.0f, 0.0f, 0.0f});
  EXPECT_FLOAT_EQ(leds.red.duty(), 0.0f);    // full on = pin low all the time
  EXPECT_FLOAT_EQ(leds.green.duty(), 1.0f);  // off = pin high all the time
  EXPECT_FLOAT_EQ(leds.blue.duty(), 1.0f);
}

TEST(RgbLed, GammaCorrectsBrightness) {
  Leds leds;
  dui::RgbLed led(&leds.red, &leds.green, &leds.blue, /*active_low=*/false);

  // Half brightness to the eye is ~22% duty (0.5^2.2), not 50%.
  led.SetRgb({0.5f, 1.0f, 0.0f});
  EXPECT_NEAR(leds.red.duty(), 0.2176f, 1e-3f);
  EXPECT_FLOAT_EQ(leds.green.duty(), 1.0f);
  EXPECT_FLOAT_EQ(leds.blue.duty(), 0.0f);
  // rgb() reports what was asked for, before correction.
  EXPECT_FLOAT_EQ(led.rgb().red, 0.5f);

  // Out-of-range brightness is clamped, not raised to a power.
  led.SetRgb({-1.0f, 2.0f, 0.0f});
  EXPECT_FLOAT_EQ(leds.red.duty(), 0.0f);
  EXPECT_FLOAT_EQ(leds.green.duty(), 1.0f);
}

TEST(RgbLed, OnOffColorsSetFullChannels) {
  Leds leds;
  dui::RgbLed led(&leds.red, &leds.green, &leds.blue, /*active_low=*/false);

  led.Set(Color::kCyan);
  EXPECT_FLOAT_EQ(leds.red.duty(), 0.0f);
  EXPECT_FLOAT_EQ(leds.green.duty(), 1.0f);
  EXPECT_FLOAT_EQ(leds.blue.duty(), 1.0f);
}

TEST(RgbLed, NamesEveryColor) {
  for (uint8_t bits = 0; bits < 8; ++bits) {
    EXPECT_STRNE(dui::ColorName(static_cast<Color>(bits)), "?");
  }
}

}  // namespace
