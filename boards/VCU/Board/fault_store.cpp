// Brake latch journal in the reserved 2 KB bank-2 page.
// Records contain a value and its complement; erase only when full.

#include <cstdint>

#include "board.hpp"
#include "board_safe_state.hpp"

namespace vcu_board {
namespace {

constexpr uint32_t kPageAddress = 0x0807F800u;  // bank 2, page 127
constexpr uint32_t kPageNumber = 127;
constexpr uint32_t kPageSize = 2048;
constexpr uint32_t kRecords = kPageSize / sizeof(uint64_t);
constexpr uint64_t kErased = ~uint64_t{0};

const volatile uint64_t* Record(uint32_t i) {
  return reinterpret_cast<const volatile uint64_t*>(kPageAddress) + i;
}

bool Valid(uint64_t r) {
  return static_cast<uint32_t>(r) == ~static_cast<uint32_t>(r >> 32);
}

// Single-bank erase units would overlap firmware, so persistence requires
// DBANK.
bool DualBank() { return (FLASH->OPTR & FLASH_OPTR_DBANK) != 0; }

}  // namespace

uint32_t LoadFaultLatch() {
  if (!DualBank()) {
    return 0;
  }
  uint32_t value = 0;
  for (uint32_t i = 0; i < kRecords; ++i) {
    const uint64_t r = *Record(i);
    if (r == kErased) {
      break;
    }
    if (Valid(r)) {
      value = static_cast<uint32_t>(r);
    }
  }
  return value;
}

void SaveFaultLatch(uint32_t value) {
  if (!DualBank() || LoadFaultLatch() == value) {
    return;
  }
  uint32_t slot = kRecords;
  for (uint32_t i = 0; i < kRecords; ++i) {
    if (*Record(i) == kErased) {
      slot = i;
      break;
    }
  }
  HAL_FLASH_Unlock();
  __HAL_FLASH_CLEAR_FLAG(FLASH_FLAG_ALL_ERRORS);
  if (slot == kRecords) {
    FLASH_EraseInitTypeDef erase = {};
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.Banks = FLASH_BANK_2;
    erase.Page = kPageNumber;
    erase.NbPages = 1;
    uint32_t bad_page = 0;
    if (HAL_FLASHEx_Erase(&erase, &bad_page) != HAL_OK) {
      HAL_FLASH_Lock();
      return;
    }
    slot = 0;
  }
  const uint64_t record =
      (static_cast<uint64_t>(~value) << 32) | static_cast<uint64_t>(value);
  HAL_FLASH_Program(FLASH_TYPEPROGRAM_DOUBLEWORD,
                    kPageAddress + slot * sizeof(uint64_t), record);
  HAL_FLASH_Lock();
}

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
