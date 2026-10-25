#pragma once

#include <cstdint>

#include "lhre_can_types.hpp"

namespace vcu {

struct Rgb {
  float r = 0.0f;
  float g = 0.0f;
  float b = 0.0f;
};

// Status RGB: solid color per state; when a fault is active (and not
// driving) the LED blinks red `code` times, then pauses, so the primary
// fault code can be read without a laptop.
//   INIT white, IDLE blue, PRECHARGE amber blink, TS_ACTIVE amber,
//   RTD_WAIT green blink, DRIVE green, FAULT red code, BSPD_TRIPPED magenta.
Rgb StatusColor(lhre::can::VcuState state, lhre::can::VcuFaultCode code,
                uint32_t now_ms);

}  // namespace vcu
