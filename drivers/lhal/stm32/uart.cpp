#include "lhal/stm32/hal.hpp"

#ifdef HAL_UART_MODULE_ENABLED

#include "lhal/stm32/uart.hpp"

namespace lhal::stm32 {
namespace {

constexpr int kMaxUarts = 8;
Uart* g_uarts[kMaxUarts] = {};

Uart* Find(UART_HandleTypeDef* huart) {
  for (Uart* u : g_uarts) {
    if (u != nullptr && u->handle() == huart) {
      return u;
    }
  }
  return nullptr;
}

}  // namespace

Uart::Uart(UART_HandleTypeDef* huart) : huart_(huart) {
  for (Uart*& slot : g_uarts) {
    if (slot == nullptr) {
      slot = this;
      return;
    }
  }
  // More UART instances than kMaxUarts: async completion won't be routed.
}

Uart::~Uart() {
  for (Uart*& slot : g_uarts) {
    if (slot == this) {
      slot = nullptr;
    }
  }
}

Status Uart::Write(const uint8_t* data, size_t len, uint32_t timeout_ms) {
  uint32_t start = HAL_GetTick();
  HAL_StatusTypeDef s;
  do {
    s = HAL_UART_Transmit(huart_, const_cast<uint8_t*>(data),
                          static_cast<uint16_t>(len), timeout_ms);
    if (s != HAL_BUSY) {
      break;
    }
  } while ((HAL_GetTick() - start) < timeout_ms);
  return ToStatus(s);
}

Status Uart::Read(uint8_t* data, size_t len, uint32_t timeout_ms) {
  HAL_StatusTypeDef s =
      HAL_UART_Receive(huart_, data, static_cast<uint16_t>(len), timeout_ms);
  // A polled read (timeout 0) can never reach the HAL's own overrun
  // handling: UART_WaitOnFlagUntilTimeout returns HAL_TIMEOUT before its
  // ORE check. The receiver then stays halted with ORE latched until the
  // flag is cleared, so an RX burst would otherwise kill reception until
  // reboot (verified on a G474 LPUART). Clear sticky reception errors on
  // any non-OK read so the next one recovers; the overrun-dropped bytes
  // are gone either way.
  if (s != HAL_OK) {
    __HAL_UART_CLEAR_FLAG(huart_, UART_CLEAR_OREF | UART_CLEAR_FEF |
                                      UART_CLEAR_NEF | UART_CLEAR_PEF);
  }
  return ToStatus(s);
}

Status Uart::WriteAsync(const uint8_t* data, size_t len,
                        CompletionCallback done, void* context) {
  tx_done_ = done;
  tx_context_ = context;
  uint8_t* p = const_cast<uint8_t*>(data);
  HAL_StatusTypeDef s =
      (huart_->hdmatx != nullptr)
          ? HAL_UART_Transmit_DMA(huart_, p, static_cast<uint16_t>(len))
          : HAL_UART_Transmit_IT(huart_, p, static_cast<uint16_t>(len));
  if (s != HAL_OK) {
    tx_done_ = nullptr;
    tx_context_ = nullptr;
  }
  return ToStatus(s);
}

Status Uart::ReadAsync(uint8_t* data, size_t len, CompletionCallback done,
                       void* context) {
  rx_done_ = done;
  rx_context_ = context;
  HAL_StatusTypeDef s =
      (huart_->hdmarx != nullptr)
          ? HAL_UART_Receive_DMA(huart_, data, static_cast<uint16_t>(len))
          : HAL_UART_Receive_IT(huart_, data, static_cast<uint16_t>(len));
  if (s != HAL_OK) {
    rx_done_ = nullptr;
    rx_context_ = nullptr;
  }
  return ToStatus(s);
}

void Uart::HandleTxComplete() {
  CompletionCallback done = tx_done_;
  void* context = tx_context_;
  tx_done_ = nullptr;
  tx_context_ = nullptr;
  if (done != nullptr) {
    done(context, Status::kOk);
  }
}

void Uart::HandleRxComplete() {
  CompletionCallback done = rx_done_;
  void* context = rx_context_;
  rx_done_ = nullptr;
  rx_context_ = nullptr;
  if (done != nullptr) {
    done(context, Status::kOk);
  }
}

void Uart::HandleError() {
  CompletionCallback tx = tx_done_;
  void* tx_ctx = tx_context_;
  CompletionCallback rx = rx_done_;
  void* rx_ctx = rx_context_;
  tx_done_ = nullptr;
  tx_context_ = nullptr;
  rx_done_ = nullptr;
  rx_context_ = nullptr;
  if (tx != nullptr) {
    tx(tx_ctx, Status::kError);
  }
  if (rx != nullptr) {
    rx(rx_ctx, Status::kError);
  }
}

}  // namespace lhal::stm32

extern "C" {

void HAL_UART_TxCpltCallback(UART_HandleTypeDef* huart) {
  if (auto* u = lhal::stm32::Find(huart)) {
    u->HandleTxComplete();
  }
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef* huart) {
  if (auto* u = lhal::stm32::Find(huart)) {
    u->HandleRxComplete();
  }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef* huart) {
  if (auto* u = lhal::stm32::Find(huart)) {
    u->HandleError();
  }
}

}  // extern "C"

#endif  // HAL_UART_MODULE_ENABLED
