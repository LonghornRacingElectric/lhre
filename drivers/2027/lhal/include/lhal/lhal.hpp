#pragma once

// Longhorn HAL (LHAL): platform-independent peripheral interfaces.
//
// Application code should depend only on these headers (//drivers/2027/lhal).
// Backends:
//   lhal/stm32/*.hpp (//drivers/2027/lhal:stm32_headers +
//   //drivers/2027/lhal:stm32_srcs) — ST HAL lhal/host/*.hpp
//   (//drivers/2027/lhal:host)                              — host fakes
// IWYU pragma: begin_exports
#include "lhal/can.hpp"
#include "lhal/gpio.hpp"
#include "lhal/i2c.hpp"
#include "lhal/status.hpp"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"
// IWYU pragma: end_exports
