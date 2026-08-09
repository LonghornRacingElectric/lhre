#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_UART_MODULE_ENABLED

#include "lhal/uart.hpp"

namespace lhal::stm32 {

// Wraps a UART_HandleTypeDef configured by board bring-up code.
//
// Async transfers use DMA when a DMA channel is linked to the handle
// (huart->hdmatx / hdmarx, i.e. the usual HAL_LINKDMA in the MSP init) and
// interrupt mode otherwise. Completion is routed through the global
// HAL_UART_{Tx,Rx}CpltCallback / HAL_UART_ErrorCallback functions, which
// lhal/stm32/uart.cpp defines — do not define them elsewhere in the project.
class Uart final : public lhal::Uart {
 public:
  explicit Uart(UART_HandleTypeDef* huart);
  ~Uart() override;

  Uart(const Uart&) = delete;
  Uart& operator=(const Uart&) = delete;

  Status Write(const uint8_t* data, size_t len, uint32_t timeout_ms) override;
  Status Read(uint8_t* data, size_t len, uint32_t timeout_ms) override;
  Status WriteAsync(const uint8_t* data, size_t len, CompletionCallback done,
                    void* context) override;
  Status ReadAsync(uint8_t* data, size_t len, CompletionCallback done,
                   void* context) override;

  // Escape hatch for anything LHAL doesn't cover.
  UART_HandleTypeDef* handle() { return huart_; }

  // Internal: ISR-side dispatch. Not for application use.
  void HandleTxComplete();
  void HandleRxComplete();
  void HandleError();

 private:
  UART_HandleTypeDef* huart_;
  CompletionCallback tx_done_ = nullptr;
  void* tx_context_ = nullptr;
  CompletionCallback rx_done_ = nullptr;
  void* rx_context_ = nullptr;
};

}  // namespace lhal::stm32

#endif  // HAL_UART_MODULE_ENABLED
