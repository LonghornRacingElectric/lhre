#if __has_include("usbd_cdc.h")

#include "lhal/stm32/usb_cdc.hpp"

#include <cstring>

#include "lhal/stm32/hal.hpp"
#include "lhal/system.hpp"

namespace lhal::stm32 {
namespace {

UsbCdc* g_cdc = nullptr;

bool TimedOut(uint32_t start_ms, uint32_t timeout_ms) {
  return timeout_ms != kWaitForever &&
         ElapsedMs(HAL_GetTick(), start_ms, timeout_ms);
}

int8_t CdcInit() {
  if (g_cdc != nullptr) {
    g_cdc->HandleInit();
  }
  return USBD_OK;
}

int8_t CdcDeInit() { return USBD_OK; }

int8_t CdcControl(uint8_t cmd, uint8_t* pbuf, uint16_t length) {
  if (g_cdc != nullptr) {
    g_cdc->HandleControl(cmd, pbuf, length);
  }
  return USBD_OK;
}

int8_t CdcReceive(uint8_t* buf, uint32_t* len) {
  if (g_cdc != nullptr) {
    g_cdc->HandleReceive(buf, *len);
  }
  return USBD_OK;
}

int8_t CdcTransmitCplt(uint8_t* /*buf*/, uint32_t* /*len*/, uint8_t /*epnum*/) {
  if (g_cdc != nullptr) {
    g_cdc->HandleTxComplete();
  }
  return USBD_OK;
}

}  // namespace

UsbCdc::UsbCdc(USBD_HandleTypeDef* handle) : handle_(handle) { g_cdc = this; }

UsbCdc::~UsbCdc() {
  if (g_cdc == this) {
    g_cdc = nullptr;
  }
}

bool UsbCdc::Configured() const {
  return handle_->dev_state == USBD_STATE_CONFIGURED;
}

bool UsbCdc::TxBusy() const {
  auto* hcdc = static_cast<USBD_CDC_HandleTypeDef*>(
      handle_->pClassDataCmsit[handle_->classId]);
  return hcdc == nullptr || hcdc->TxState != 0U;
}

bool UsbCdc::connected() const { return Configured() && dtr_; }

Status UsbCdc::Write(const uint8_t* data, size_t len, uint32_t timeout_ms) {
  if (!Configured()) {
    return Status::kError;
  }
  uint32_t start = HAL_GetTick();
  while (TxBusy()) {
    if (TimedOut(start, timeout_ms)) {
      return Status::kTimeout;
    }
  }
  USBD_CDC_SetTxBuffer(handle_, const_cast<uint8_t*>(data),
                       static_cast<uint32_t>(len));
  if (USBD_CDC_TransmitPacket(handle_) != USBD_OK) {
    return Status::kError;
  }
  // `data` is read by the endpoint while the transfer runs; block until it
  // drains so the caller's buffer may go out of scope.
  while (TxBusy()) {
    if (TimedOut(start, timeout_ms)) {
      return Status::kTimeout;
    }
  }
  return Status::kOk;
}

Status UsbCdc::Read(uint8_t* data, size_t len, uint32_t timeout_ms) {
  uint32_t start = HAL_GetTick();
  size_t got = 0;
  while (got < len) {
    while (rx_tail_ == rx_head_) {
      if (TimedOut(start, timeout_ms)) {
        return Status::kTimeout;
      }
    }
    data[got++] = rx_ring_[rx_tail_ % kRxBufferSize];
    rx_tail_ = rx_tail_ + 1;
  }
  return Status::kOk;
}

Status UsbCdc::WriteAsync(const uint8_t* data, size_t len,
                          CompletionCallback done, void* context) {
  if (!Configured() || TxBusy()) {
    return Status::kBusy;
  }
  tx_done_ = done;
  tx_context_ = context;
  USBD_CDC_SetTxBuffer(handle_, const_cast<uint8_t*>(data),
                       static_cast<uint32_t>(len));
  if (USBD_CDC_TransmitPacket(handle_) != USBD_OK) {
    tx_done_ = nullptr;
    tx_context_ = nullptr;
    return Status::kError;
  }
  return Status::kOk;
}

Status UsbCdc::ReadAsync(uint8_t* data, size_t len, CompletionCallback done,
                         void* context) {
  if (rx_async_len_ != 0) {
    return Status::kBusy;
  }
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  // Satisfy as much as possible from bytes already buffered.
  size_t pos = 0;
  while (pos < len && rx_tail_ != rx_head_) {
    data[pos++] = rx_ring_[rx_tail_ % kRxBufferSize];
    rx_tail_ = rx_tail_ + 1;
  }
  Status result = Status::kOk;
  bool complete = pos == len;
  if (!complete) {
    rx_async_data_ = data;
    rx_async_pos_ = pos;
    rx_async_done_ = done;
    rx_async_context_ = context;
    rx_async_len_ = len;  // written last: marks the pending read live
  }
  if (primask == 0U) {
    __enable_irq();
  }
  if (complete && done != nullptr) {
    done(context, Status::kOk);
  }
  return result;
}

void UsbCdc::HandleInit() {
  rx_head_ = 0;
  rx_tail_ = 0;
  dtr_ = false;
  USBD_CDC_SetTxBuffer(handle_, nullptr, 0);
  USBD_CDC_SetRxBuffer(handle_, rx_packet_);
}

void UsbCdc::HandleReceive(uint8_t* data, uint32_t len) {
  size_t i = 0;
  // A pending ReadAsync consumes directly.
  if (rx_async_len_ != 0) {
    while (i < len && rx_async_pos_ < rx_async_len_) {
      rx_async_data_[rx_async_pos_++] = data[i++];
    }
    if (rx_async_pos_ == rx_async_len_) {
      CompletionCallback done = rx_async_done_;
      void* context = rx_async_context_;
      rx_async_len_ = 0;
      rx_async_done_ = nullptr;
      rx_async_context_ = nullptr;
      if (done != nullptr) {
        done(context, Status::kOk);
      }
    }
  }
  // The rest goes into the ring; drop the newest bytes on overflow.
  for (; i < len; ++i) {
    if (rx_head_ - rx_tail_ >= kRxBufferSize) {
      break;
    }
    rx_ring_[rx_head_ % kRxBufferSize] = data[i];
    rx_head_ = rx_head_ + 1;
  }
  // Re-arm reception for the next OUT packet.
  USBD_CDC_SetRxBuffer(handle_, rx_packet_);
  USBD_CDC_ReceivePacket(handle_);
}

void UsbCdc::HandleTxComplete() {
  CompletionCallback done = tx_done_;
  void* context = tx_context_;
  tx_done_ = nullptr;
  tx_context_ = nullptr;
  if (done != nullptr) {
    done(context, Status::kOk);
  }
}

void UsbCdc::HandleControl(uint8_t cmd, uint8_t* pbuf, uint16_t length) {
  switch (cmd) {
    case CDC_SET_LINE_CODING:
      if (length >= sizeof(line_coding_)) {
        std::memcpy(line_coding_, pbuf, sizeof(line_coding_));
      }
      break;
    case CDC_GET_LINE_CODING:
      if (length >= sizeof(line_coding_)) {
        std::memcpy(pbuf, line_coding_, sizeof(line_coding_));
      }
      break;
    case CDC_SET_CONTROL_LINE_STATE: {
      // Zero-length requests pass the raw setup packet; DTR is wValue bit 0.
      auto* req = reinterpret_cast<USBD_SetupReqTypedef*>(pbuf);
      dtr_ = (req->wValue & 0x1U) != 0U;
      break;
    }
    default:
      break;
  }
}

}  // namespace lhal::stm32

// The CDC interface struct the CubeMX-generated MX_USB_Device_Init registers
// (its declaration lives in the generated usbd_cdc_if.h). Defining it here
// replaces the generated usbd_cdc_if.c wholesale — do not compile that file.
extern "C" {
USBD_CDC_ItfTypeDef USBD_Interface_fops_FS = {
    lhal::stm32::CdcInit,         lhal::stm32::CdcDeInit,
    lhal::stm32::CdcControl,      lhal::stm32::CdcReceive,
    lhal::stm32::CdcTransmitCplt,
};
}  // extern "C"

#endif  // __has_include("usbd_cdc.h")
