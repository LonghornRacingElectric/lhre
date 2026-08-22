#pragma once

// Present only when ST's USB Device middleware is on the include path
// (//drivers/stm32/usb_device:headers). Boards without USB compile this —
// and usb_cdc.cpp — to nothing.
#if __has_include("usbd_cdc.h")

#include <cstddef>
#include <cstdint>

#include "lhal/uart.hpp"
#include "usbd_cdc.h"

namespace lhal::stm32 {

// USB CDC-ACM (virtual COM port) as an lhal::Uart byte stream.
//
// Wraps the USBD_HandleTypeDef configured by CubeMX bring-up (usb_device.c,
// usbd_conf.c, usbd_desc.c — keep those generated files as-is). The matching
// usb_cdc.cpp defines USBD_Interface_fops_FS, the CDC interface struct that
// the generated MX_USB_Device_Init registers, so exclude the generated
// USB_DEVICE/App/usbd_cdc_if.c from the board's sources — its only job was
// to define that struct.
//
// Construct the instance BEFORE calling MX_USB_Device_Init so reception is
// routed from the first packet. Single instance only: the CDC interface
// callbacks carry no device pointer (and MCUs have one USB device
// peripheral).
//
// RX bytes are buffered from USB interrupt context into an internal ring;
// Read() drains it. Overflow drops the newest bytes. Write() blocks until
// the transfer is on the wire or the timeout expires.
class UsbCdc final : public lhal::Uart {
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

  // Escape hatch for anything LHAL doesn't cover.
  USBD_HandleTypeDef* handle() { return handle_; }

  // Internal: CDC interface dispatch, runs in USB interrupt context. Not
  // for application use.
  void HandleInit();
  void HandleReceive(uint8_t* data, uint32_t len);
  void HandleTxComplete();
  void HandleControl(uint8_t cmd, uint8_t* pbuf, uint16_t length);

 private:
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

}  // namespace lhal::stm32

#endif  // __has_include("usbd_cdc.h")
