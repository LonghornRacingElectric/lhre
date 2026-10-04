// Brake comparator soft latch persistence. The rule is that a power cycle
// must not clear it, so it lives in the last 2 KB flash page of bank 2
// (reserved in STM32G474XX_FLASH.ld). Records are append-only double
// words: low word = value, high word = its complement. An erase happens
// only when the page is full, from the telemetry task (never the control
// loop). In dual-bank mode, writing bank 2 doesn't stall code running from
// bank 1.

#include <cstdint>

#include "board.hpp"

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

// The page layout above assumes 2 KB pages, i.e. dual-bank mode (the
// option-byte default). In single-bank mode pages are 4 KB and this page
// would share an erase unit with code, so persistence is disabled.
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
  // BOOT0 = SW1 OR (PB7 through R16 47k into C18 100 nF). Hold PB7 high
  // for several RC constants, then reset; the ROM samples BOOT0 before C18
  // discharges. 50 ms matches lhre-2026 (app_freertos.c:255-264).
  __disable_irq();
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
  for (volatile uint32_t i = 0; i < SystemCoreClock / 20 / 4; ++i) {
  }
  NVIC_SystemReset();
}

}  // namespace vcu_board
