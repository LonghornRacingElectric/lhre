#pragma once

#include <cstddef>
#include <cstdint>

#include "lhal/status.hpp"

namespace lhal {

class Uart {
 public:
  virtual ~Uart() = default;

  using RxObserver = void (*)(void*, const uint8_t*, size_t);
  // Optional receive-path observer, before buffering (including overflow).
  // May run in ISR context; must not block. Context must outlive registration.
  // Returns false when unsupported; callers can observe polled bytes instead.
  virtual bool SetRxObserver(RxObserver, void*) { return false; }

  // Blocking transfers. Return kTimeout if the transfer did not finish in
  // time; a partial transfer may have occurred.
  virtual Status Write(const uint8_t* data, size_t len,
                       uint32_t timeout_ms) = 0;
  virtual Status Read(uint8_t* data, size_t len, uint32_t timeout_ms) = 0;

  // Non-blocking transfers. `data` must stay valid until `done` fires. On
  // STM32 these use DMA when a DMA channel is linked to the handle and
  // interrupts otherwise; `done` may run in interrupt context.
  virtual Status WriteAsync(const uint8_t* data, size_t len,
                            CompletionCallback done, void* context) = 0;
  virtual Status ReadAsync(uint8_t* data, size_t len, CompletionCallback done,
                           void* context) = 0;

  // True while a receiver is listening on the stream. Always true for raw
  // UART; on USB CDC it reflects enumeration and host port-open (DTR asserted).
  virtual bool connected() const { return true; }
};

}  // namespace lhal
