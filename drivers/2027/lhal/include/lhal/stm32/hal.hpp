#pragma once

// Pulls in the ST HAL for whichever STM32 family this target is built for.
// The board provides the family HAL and its <family>_hal_conf.h on the
// include path (firmware_project already does this), so no extra defines are
// needed here.
#if __has_include("stm32g4xx_hal.h")
#include "stm32g4xx_hal.h"
#elif __has_include("stm32h7xx_hal.h")
#include "stm32h7xx_hal.h"
#elif __has_include("stm32f4xx_hal.h")
#include "stm32f4xx_hal.h"
#elif __has_include("stm32f0xx_hal.h")
#include "stm32f0xx_hal.h"
#else
#error \
    "lhal/stm32: no STM32 HAL header on the include path (depend on //drivers/stm32/<family>:headers)"
#endif

#include "lhal/status.hpp"

namespace lhal::stm32 {

constexpr Status ToStatus(HAL_StatusTypeDef s) {
  switch (s) {
    case HAL_OK:
      return Status::kOk;
    case HAL_BUSY:
      return Status::kBusy;
    case HAL_TIMEOUT:
      return Status::kTimeout;
    default:
      return Status::kError;
  }
}

}  // namespace lhal::stm32
