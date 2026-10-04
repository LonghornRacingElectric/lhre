#pragma once

#include "lhal/stm32/hal.hpp"

// Register-level: the IWDG needs no HAL module (and CubeMX boards that don't
// enable it in the .ioc have no HAL_IWDG_MODULE_ENABLED), so this is guarded
// on the peripheral existing instead.
#if defined(IWDG) && defined(LSI_VALUE)

#include <cstdint>

#include "lhal/watchdog.hpp"

namespace lhal::stm32 {

// Independent watchdog, clocked from the LSI. Once started it cannot be
// stopped; it keeps running through a reset, so the next boot must refresh
// it within the timeout.
class Iwdg final : public lhal::Watchdog {
 public:
  Status Start(uint32_t timeout_ms) override {
    // Prescaler codes 0..6 divide the LSI by 4..256; reload is 12 bits.
    for (uint32_t code = 0; code <= 6; ++code) {
      const uint32_t divider = 4u << code;
      const uint64_t ticks = static_cast<uint64_t>(timeout_ms) * LSI_VALUE /
                             (static_cast<uint64_t>(divider) * 1000u);
      if (ticks >= 1 && ticks <= 0x1000u) {
        IWDG->KR = kKeyStart;
        IWDG->KR = kKeyUnlock;
        IWDG->PR = code;
        IWDG->RLR = static_cast<uint32_t>(ticks - 1);
        // Register updates cross into the LSI domain; bounded wait.
        for (uint32_t i = 0; i < kUpdateSpins && IWDG->SR != 0; ++i) {
        }
        IWDG->KR = kKeyRefresh;
        return IWDG->SR == 0 ? Status::kOk : Status::kTimeout;
      }
    }
    return Status::kError;
  }

  void Refresh() override { IWDG->KR = kKeyRefresh; }

 private:
  static constexpr uint32_t kKeyRefresh = 0xAAAAu;
  static constexpr uint32_t kKeyUnlock = 0x5555u;
  static constexpr uint32_t kKeyStart = 0xCCCCu;
  static constexpr uint32_t kUpdateSpins = 100000u;
};

}  // namespace lhal::stm32

#endif  // IWDG && LSI_VALUE
