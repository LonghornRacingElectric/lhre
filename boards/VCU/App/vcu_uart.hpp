#pragma once
#include "lhal/uart.hpp"
namespace vcu {
class RecoveryUart : public lhal::Uart {
 public:
  using RxObserver = void (*)(void*, const uint8_t*, size_t);
  virtual bool SetRxObserver(RxObserver, void*) = 0;
};
}  // namespace vcu
