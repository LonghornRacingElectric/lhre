#pragma once

// Longhorn HAL (LHAL): platform-independent peripheral interfaces.
//
// Application code should depend only on these headers (//drivers/lhal).
// Backends:
//   lhal/stm32/*.hpp — ST HAL adapters (//drivers/lhal:stm32_headers
//                      + //drivers/lhal:stm32_srcs)
//   lhal/host/*.hpp  — host fakes      (//drivers/lhal:host)
// IWYU pragma: begin_exports
#include "lhal/can.hpp"
#include "lhal/gpio.hpp"
#include "lhal/i2c.hpp"
#include "lhal/pwm.hpp"
#include "lhal/status.hpp"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"
// IWYU pragma: end_exports
