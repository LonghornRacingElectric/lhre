#pragma once

#include "lhal/stm32/hal.hpp"

#if defined(RCC_CSR_IWDGRSTF) && defined(RCC_CSR_RMVF)

#include "lhal/reset.hpp"

namespace lhal::stm32 {

// Reads RCC->CSR's reset flags, then clears them so the next reset reports
// fresh. Call once, early in main(). Every internal reset also drives NRST,
// so PINRSTF is checked last; a power-on sets BORRSTF.
inline ResetCause ReadAndClearResetCause() {
  const uint32_t csr = RCC->CSR;
  RCC->CSR |= RCC_CSR_RMVF;
  if ((csr & RCC_CSR_IWDGRSTF) != 0) {
    return ResetCause::kIndependentWatchdog;
  }
  if ((csr & RCC_CSR_WWDGRSTF) != 0) {
    return ResetCause::kWindowWatchdog;
  }
  if ((csr & RCC_CSR_SFTRSTF) != 0) {
    return ResetCause::kSoftware;
  }
  if ((csr & RCC_CSR_LPWRRSTF) != 0) {
    return ResetCause::kLowPower;
  }
#if defined(RCC_CSR_OBLRSTF)
  if ((csr & RCC_CSR_OBLRSTF) != 0) {
    return ResetCause::kOptionByteLoad;
  }
#endif
#if defined(RCC_CSR_BORRSTF)
  if ((csr & RCC_CSR_BORRSTF) != 0) {
    return ResetCause::kPowerOn;
  }
#endif
  if ((csr & RCC_CSR_PINRSTF) != 0) {
    return ResetCause::kPin;
  }
  return ResetCause::kUnknown;
}

}  // namespace lhal::stm32

#endif  // RCC_CSR_IWDGRSTF && RCC_CSR_RMVF
