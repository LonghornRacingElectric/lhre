#pragma once

#include "lhal/stm32/hal.hpp"

// CubeMX-generated clock configuration, from the board's Core/Src/main.c.
// With ProjectManager.NoMain=true, CubeMX emits main.c *without* a main()
// but still generates SystemClock_Config() (and Error_Handler()) there, so
// clock-tree changes made in CubeMX flow into the firmware on regeneration
// with no hand transcription.
extern "C" void SystemClock_Config(void);

namespace lhal::stm32 {

// Standard board bring-up: HAL init + the CubeMX-generated clock config.
// Call once at the top of main(), before touching any peripheral. Requires
// the board to compile its generated Core/Src/main.c (see above).
inline void InitCore() {
  HAL_Init();
  SystemClock_Config();
}

}  // namespace lhal::stm32
