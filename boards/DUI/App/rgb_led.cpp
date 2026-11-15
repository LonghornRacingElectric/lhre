#include "rgb_led.hpp"

#include <cmath>

namespace dui {

const char* ColorName(Color color) {
  switch (color) {
    case Color::kOff:
      return "off";
    case Color::kRed:
      return "red";
    case Color::kGreen:
      return "green";
    case Color::kYellow:
      return "yellow";
    case Color::kBlue:
      return "blue";
    case Color::kMagenta:
      return "magenta";
    case Color::kCyan:
      return "cyan";
    case Color::kWhite:
      return "white";
  }
  return "?";
}

Rgb HueToRgb(float hue) {
  hue -= static_cast<float>(static_cast<int>(hue));  // wrap into [0, 1)
  if (hue < 0.0f) {
    hue += 1.0f;
  }
  // Six 60-degree sectors; in each, one channel is full, one is off, and
  // the third ramps between them.
  const float h6 = hue * 6.0f;
  const int sector = static_cast<int>(h6);
  const float ramp = h6 - static_cast<float>(sector);
  switch (sector) {
    case 0:
      return {1.0f, ramp, 0.0f};  // red -> yellow
    case 1:
      return {1.0f - ramp, 1.0f, 0.0f};  // yellow -> green
    case 2:
      return {0.0f, 1.0f, ramp};  // green -> cyan
    case 3:
      return {0.0f, 1.0f - ramp, 1.0f};  // cyan -> blue
    case 4:
      return {ramp, 0.0f, 1.0f};  // blue -> magenta
    default:
      return {1.0f, 0.0f, 1.0f - ramp};  // magenta -> red
  }
}

RgbLed::RgbLed(lhal::Pwm* red, lhal::Pwm* green, lhal::Pwm* blue,
               bool active_low)
    : red_(red), green_(green), blue_(blue), active_low_(active_low) {
  // Drive every channel to a known state before anything starts the timer.
  SetRgb({});
}

void RgbLed::SetRgb(const Rgb& rgb) {
  Drive(red_, rgb.red);
  Drive(green_, rgb.green);
  Drive(blue_, rgb.blue);
  rgb_ = rgb;
}

void RgbLed::Set(Color color) {
  const auto bits = static_cast<uint8_t>(color);
  SetRgb({(bits & 0b001) ? 1.0f : 0.0f, (bits & 0b010) ? 1.0f : 0.0f,
          (bits & 0b100) ? 1.0f : 0.0f});
}

void RgbLed::Drive(lhal::Pwm* channel, float brightness) {
  if (channel == nullptr) {
    return;
  }
  if (brightness < 0.0f) {
    brightness = 0.0f;
  } else if (brightness > 1.0f) {
    brightness = 1.0f;
  }
  const float duty = std::pow(brightness, kGamma);
  channel->SetDuty(active_low_ ? 1.0f - duty : duty);
}

}  // namespace dui
