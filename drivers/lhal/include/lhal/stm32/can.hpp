#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_FDCAN_MODULE_ENABLED

#include "lhal/can.hpp"

namespace lhal::stm32 {

// Wraps an FDCAN_HandleTypeDef configured by board bring-up code (filters
// included). Call Start() once after construction: it starts the peripheral
// and enables the RX FIFO0 (new message, message lost), TX FIFO empty, and
// bus-off interrupts on interrupt line 0 — the board enables that IRQ in the
// NVIC (FDCANx_IT0) at or below configMAX_SYSCALL_INTERRUPT_PRIORITY if
// tasks are woken from callbacks.
//
// The hardware TX FIFO holds only a few frames, so Send() falls back to a
// software queue (kTxQueueDepth) that the TX FIFO empty interrupt drains,
// in order. A bus-off is recovered automatically (re-enter normal operation
// and let the controller count 128 x 11 recessive bits) and counted in
// stats().
//
// Callback dispatch goes through the global HAL_FDCAN_RxFifo0Callback,
// HAL_FDCAN_TxFifoEmptyCallback and HAL_FDCAN_ErrorStatusCallback, which
// lhal/stm32/can.cpp defines — do not define them elsewhere.
class Can final : public lhal::CanBus {
 public:
  static constexpr size_t kTxQueueDepth = 32;

  explicit Can(FDCAN_HandleTypeDef* hfdcan);
  ~Can() override;

  Can(const Can&) = delete;
  Can& operator=(const Can&) = delete;

  Status Start();

  Status Send(const CanFrame& frame) override;
  bool Receive(CanFrame* out) override;
  void SetRxCallback(RxCallback callback, void* context) override;
  CanStats stats() const override;

  // Escape hatch for anything LHAL doesn't cover.
  FDCAN_HandleTypeDef* handle() { return hfdcan_; }

  // Internal: ISR-side dispatch. Not for application use.
  void HandleRxFifo0();
  void HandleRxFifo0Lost();
  void HandleTxFifoEmpty();
  void HandleBusOff();

 private:
  bool ReadFifo0(CanFrame* out);
  // Moves queued frames into the hardware FIFO while it has room. Caller
  // holds interrupts off (or runs in this instance's ISR).
  void DrainTxQueue();
  bool AddToHardware(const CanFrame& frame);

  FDCAN_HandleTypeDef* hfdcan_;
  RxCallback rx_callback_ = nullptr;
  void* rx_context_ = nullptr;

  CanFrame tx_queue_[kTxQueueDepth];
  size_t tx_head_ = 0;  // next to send
  size_t tx_count_ = 0;
  volatile uint32_t bus_off_count_ = 0;
  volatile uint32_t tx_dropped_ = 0;
  volatile uint32_t rx_lost_ = 0;
};

}  // namespace lhal::stm32

#endif  // HAL_FDCAN_MODULE_ENABLED
