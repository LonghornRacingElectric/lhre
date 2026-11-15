#pragma once

// Longhorn HAL (LHAL): platform-independent peripheral interfaces.
//
// Application code should depend only on these headers (//drivers/lhal).
// Backends:
//   lhal/stm32/*.hpp — ST HAL adapters (//drivers/lhal:stm32_headers
//                      + //drivers/lhal:stm32_srcs)
//   lhal/host/*.hpp  — host fakes      (//drivers/lhal:host)
// IWYU pragma: begin_exports
#include "lhal/adc.hpp"
#include "lhal/can.hpp"
#include "lhal/dac.hpp"
#include "lhal/gpio.hpp"
#include "lhal/i2c.hpp"
#include "lhal/pwm.hpp"
#include "lhal/reset.hpp"
#include "lhal/status.hpp"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"
#include "lhal/watchdog.hpp"
// IWYU pragma: end_exports
