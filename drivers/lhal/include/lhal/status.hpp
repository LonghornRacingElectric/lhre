#pragma once

#include <cstdint>

namespace lhal {

// Result of an LHAL operation. Mirrors the shape of HAL_StatusTypeDef so the
// STM32 backend can translate losslessly.
enum class Status : uint8_t {
  kOk = 0,
  kError,
  kBusy,
  kTimeout,
};

constexpr bool IsOk(Status s) { return s == Status::kOk; }

// Completion callback for asynchronous transfers. May be invoked from
// interrupt context on embedded targets: keep it short and ISR-safe.
using CompletionCallback = void (*)(void* context, Status status);

inline constexpr uint32_t kWaitForever = 0xFFFFFFFFu;

}  // namespace lhal
