#pragma once

#include <cstdint>

#include "lhal/pwm.hpp"

namespace dui {

// The eight on/off colors. Bit 0 is red, bit 1 green, bit 2 blue, so mixed
// colors are just the OR of their channels. Handy for status colors later
// (e.g. red for a latched fault); the rainbow uses SetRgb() directly.
enum class Color : uint8_t {
  kOff = 0b000,
  kRed = 0b001,
  kGreen = 0b010,
  kYellow = 0b011,
  kBlue = 0b100,
  kMagenta = 0b101,
  kCyan = 0b110,
  kWhite = 0b111,
};

const char* ColorName(Color color);

// Brightness of each channel, 0 (off) to 1 (full).
struct Rgb {
  float red = 0.0f;
  float green = 0.0f;
  float blue = 0.0f;
};

// Fully saturated color at `hue` around the color wheel: 0 is red, 1/6
// yellow, 1/3 green, 1/2 cyan, 2/3 blue, 5/6 magenta, wrapping at 1.
Rgb HueToRgb(float hue);

// One RGB LED on three PWM channels, one per color. The DUI's LED (D11) is
// common-anode: the anode sits on Vdd and each MCU pin sinks its channel's
// current, so a channel is lit while its pin is LOW. With `active_low`,
// brightness b becomes duty 1 - b, which lets the timer keep CubeMX's
// default (active-high) polarity. Brightness is gamma-corrected (see
// kGamma) so equal steps in brightness look like equal steps to the eye,
// which is what makes a fade between colors look smooth. Null channels are
// skipped, so tests and a partly wired LED still work.
class RgbLed {
 public:
  // Standard display gamma. Duty = brightness^kGamma: half brightness is
  // ~22% duty, because the eye is far more sensitive to changes near dark.
  static constexpr float kGamma = 2.2f;

  RgbLed(lhal::Pwm* red, lhal::Pwm* green, lhal::Pwm* blue, bool active_low);

  void SetRgb(const Rgb& rgb);
  void Set(Color color);
  const Rgb& rgb() const { return rgb_; }

 private:
  void Drive(lhal::Pwm* channel, float brightness);

  lhal::Pwm* red_;
  lhal::Pwm* green_;
  lhal::Pwm* blue_;
  bool active_low_;
  Rgb rgb_;
};

}  // namespace dui
