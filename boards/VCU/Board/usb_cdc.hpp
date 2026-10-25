#pragma once

// Present only when ST's USB Device middleware is on the include path
// (//drivers/stm32/usb_device:headers). Boards without USB compile this —
// and usb_cdc.cpp — to nothing.
#if __has_include("usbd_cdc.h")

#include <cstddef>
#include <cstdint>

#include "usbd_cdc.h"
#include "vcu_uart.hpp"

extern "C" USBD_CDC_ItfTypeDef VcuUsbInterface;

namespace vcu_board {
using namespace lhal;

// VCU USB CDC stream. Board/main.cpp registers VcuUsbInterface before
// starting USB. The RX observer detects DFU commands independently of
// shell/control progress. One instance; construct it before USB starts.
class UsbCdc final : public vcu::RecoveryUart {
 public:
  static constexpr size_t kRxBufferSize = 1024;  // power of two

  explicit UsbCdc(USBD_HandleTypeDef* handle);
  ~UsbCdc() override;

  UsbCdc(const UsbCdc&) = delete;
  UsbCdc& operator=(const UsbCdc&) = delete;

  Status Write(const uint8_t* data, size_t len, uint32_t timeout_ms) override;
  Status Read(uint8_t* data, size_t len, uint32_t timeout_ms) override;
  Status WriteAsync(const uint8_t* data, size_t len, CompletionCallback done,
                    void* context) override;
  Status ReadAsync(uint8_t* data, size_t len, CompletionCallback done,
                   void* context) override;

  // True while the device is enumerated and the host has the port open
  // (DTR asserted) — useful to skip logging when no terminal is attached.
  bool connected() const override;
  bool SetRxObserver(RxObserver observer, void* context) override;

  // Escape hatch for anything LHAL doesn't cover.
  USBD_HandleTypeDef* handle() { return handle_; }

  // Internal: CDC interface dispatch, runs in USB interrupt context. Not
  // for application use.
  void HandleInit();
  void HandleReceive(uint8_t* data, uint32_t len);
  void HandleTxComplete();
  void HandleControl(uint8_t cmd, uint8_t* pbuf, uint16_t length);

 private:
  RxObserver rx_observer_ = nullptr;
  void* rx_observer_context_ = nullptr;
  bool Configured() const;
  bool TxBusy() const;

  USBD_HandleTypeDef* handle_;

  // Staging buffer the class DMA/FIFO writes each OUT packet into.
  alignas(4) uint8_t rx_packet_[CDC_DATA_HS_MAX_PACKET_SIZE];

  // ISR-producer / thread-consumer ring; head_ is only written by the ISR,
  // tail_ only by the reader.
  uint8_t rx_ring_[kRxBufferSize];
  volatile uint32_t rx_head_ = 0;
  volatile uint32_t rx_tail_ = 0;

  CompletionCallback tx_done_ = nullptr;
  void* tx_context_ = nullptr;

  // Pending ReadAsync target, filled from interrupt context.
  uint8_t* rx_async_data_ = nullptr;
  volatile size_t rx_async_len_ = 0;
  size_t rx_async_pos_ = 0;
  CompletionCallback rx_async_done_ = nullptr;
  void* rx_async_context_ = nullptr;

  // Line coding echoed back to the host (baud etc. is meaningless for USB,
  // but terminals expect GET_LINE_CODING to work). Default 115200 8N1.
  uint8_t line_coding_[7] = {0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08};
  volatile bool dtr_ = false;
};

}  // namespace vcu_board

#endif  // __has_include("usbd_cdc.h")
