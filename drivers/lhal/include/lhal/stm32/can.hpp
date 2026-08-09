#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_FDCAN_MODULE_ENABLED

#include "lhal/can.hpp"

namespace lhal::stm32 {

// Wraps an FDCAN_HandleTypeDef configured by board bring-up code (filters
// included). Call Start() once after construction: it starts the peripheral
// and enables RX FIFO0 new-message interrupts.
//
// RX callback dispatch goes through the global HAL_FDCAN_RxFifo0Callback,
// which lhal/stm32/can.cpp defines — do not define it elsewhere.
class Can final : public lhal::CanBus {
 public:
  explicit Can(FDCAN_HandleTypeDef* hfdcan);
  ~Can() override;

  Can(const Can&) = delete;
  Can& operator=(const Can&) = delete;

  Status Start();

  Status Send(const CanFrame& frame) override;
  bool Receive(CanFrame* out) override;
  void SetRxCallback(RxCallback callback, void* context) override;

  // Escape hatch for anything LHAL doesn't cover.
  FDCAN_HandleTypeDef* handle() { return hfdcan_; }

  // Internal: ISR-side dispatch. Not for application use.
  void HandleRxFifo0();

 private:
  bool ReadFifo0(CanFrame* out);

  FDCAN_HandleTypeDef* hfdcan_;
  RxCallback rx_callback_ = nullptr;
  void* rx_context_ = nullptr;
};

}  // namespace lhal::stm32

#endif  // HAL_FDCAN_MODULE_ENABLED
