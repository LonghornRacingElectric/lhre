// Bootloader entry.

#include <cstdint>

#include "board.hpp"
#include "board_safe_state.hpp"

namespace vcu_board {

[[noreturn]] void EnterBootloader() {
  // Hold PB7 high for 50 ms to charge the BOOT0 RC before reset.
  ForceSafeOutputs();
  // Last year's ota_set_bank1 cleared BFB2 before USB flashed 0x08000000.
  // Preserve that recovery behavior without rewriting unchanged options.
  IWDG->KR = 0xAAAAu;
  if ((FLASH->OPTR & FLASH_OPTR_BFB2) != 0) {
    __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_OPTVERR);
    FLASH_OBProgramInitTypeDef options = {};
    options.OptionType = OPTIONBYTE_USER;
    options.USERType = OB_USER_BFB2;
    options.USERConfig = OB_BFB2_DISABLE;
    if (HAL_FLASH_Unlock() != HAL_OK || HAL_FLASH_OB_Unlock() != HAL_OK ||
        HAL_FLASHEx_OBProgram(&options) != HAL_OK) {
      Error_Handler();
    }
    HAL_FLASH_OB_Lock();
    HAL_FLASH_Lock();
  }
  __disable_irq();
  IWDG->KR = 0xAAAAu;
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
  // Count actual CPU cycles: an optimized C spin loop is not a timebase.
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
  const uint32_t start = DWT->CYCCNT;
  while (static_cast<uint32_t>(DWT->CYCCNT - start) < SystemCoreClock / 20) {
  }
  __DSB();
  NVIC_SystemReset();
}

}  // namespace vcu_board
