#include "lhal/stm32/hal.hpp"

#ifdef HAL_FDCAN_MODULE_ENABLED

#include "lhal/stm32/can.hpp"

namespace lhal::stm32 {
namespace {

constexpr int kMaxCans = 4;
Can* g_cans[kMaxCans] = {};

Can* Find(FDCAN_HandleTypeDef* hfdcan) {
  for (Can* c : g_cans) {
    if (c != nullptr && c->handle() == hfdcan) {
      return c;
    }
  }
  return nullptr;
}

// FDCAN DLC values are opaque HAL constants; map through a table instead of
// assuming their encoding.
struct DlcEntry {
  uint8_t len;
  uint32_t dlc;
};

constexpr DlcEntry kDlcTable[] = {
    {0, FDCAN_DLC_BYTES_0},   {1, FDCAN_DLC_BYTES_1},
    {2, FDCAN_DLC_BYTES_2},   {3, FDCAN_DLC_BYTES_3},
    {4, FDCAN_DLC_BYTES_4},   {5, FDCAN_DLC_BYTES_5},
    {6, FDCAN_DLC_BYTES_6},   {7, FDCAN_DLC_BYTES_7},
    {8, FDCAN_DLC_BYTES_8},   {12, FDCAN_DLC_BYTES_12},
    {16, FDCAN_DLC_BYTES_16}, {20, FDCAN_DLC_BYTES_20},
    {24, FDCAN_DLC_BYTES_24}, {32, FDCAN_DLC_BYTES_32},
    {48, FDCAN_DLC_BYTES_48}, {64, FDCAN_DLC_BYTES_64},
};

// Smallest DLC that fits `len` (FDCAN lengths are quantized above 8 bytes;
// padding bytes come from the caller's frame.data, zero by default).
uint32_t LenToDlc(uint8_t len) {
  for (const DlcEntry& e : kDlcTable) {
    if (e.len >= len) {
      return e.dlc;
    }
  }
  return FDCAN_DLC_BYTES_64;
}

uint8_t DlcToLen(uint32_t dlc) {
  for (const DlcEntry& e : kDlcTable) {
    if (e.dlc == dlc) {
      return e.len;
    }
  }
  return 0;
}

}  // namespace

Can::Can(FDCAN_HandleTypeDef* hfdcan) : hfdcan_(hfdcan) {
  for (Can*& slot : g_cans) {
    if (slot == nullptr) {
      slot = this;
      return;
    }
  }
}

Can::~Can() {
  for (Can*& slot : g_cans) {
    if (slot == this) {
      slot = nullptr;
    }
  }
}

Status Can::Start() {
  HAL_StatusTypeDef s = HAL_FDCAN_Start(hfdcan_);
  if (s != HAL_OK) {
    return ToStatus(s);
  }
  return ToStatus(HAL_FDCAN_ActivateNotification(
      hfdcan_, FDCAN_IT_RX_FIFO0_NEW_MESSAGE, 0));
}

Status Can::Send(const CanFrame& frame) {
  FDCAN_TxHeaderTypeDef header = {};
  header.Identifier = frame.id;
  header.IdType = frame.extended_id ? FDCAN_EXTENDED_ID : FDCAN_STANDARD_ID;
  header.TxFrameType = FDCAN_DATA_FRAME;
  header.DataLength = LenToDlc(frame.len);
  header.ErrorStateIndicator = FDCAN_ESI_ACTIVE;
  header.BitRateSwitch = frame.bitrate_switch ? FDCAN_BRS_ON : FDCAN_BRS_OFF;
  header.FDFormat = frame.fd ? FDCAN_FD_CAN : FDCAN_CLASSIC_CAN;
  header.TxEventFifoControl = FDCAN_NO_TX_EVENTS;
  header.MessageMarker = 0;
  return ToStatus(HAL_FDCAN_AddMessageToTxFifoQ(
      hfdcan_, &header, const_cast<uint8_t*>(frame.data)));
}

bool Can::Receive(CanFrame* out) { return ReadFifo0(out); }

void Can::SetRxCallback(RxCallback callback, void* context) {
  rx_callback_ = callback;
  rx_context_ = context;
}

void Can::HandleRxFifo0() {
  // No callback set: leave frames in the hardware FIFO for polled Receive().
  if (rx_callback_ == nullptr) {
    return;
  }
  CanFrame frame;
  while (ReadFifo0(&frame)) {
    rx_callback_(rx_context_, frame);
  }
}

bool Can::ReadFifo0(CanFrame* out) {
  if (HAL_FDCAN_GetRxFifoFillLevel(hfdcan_, FDCAN_RX_FIFO0) == 0) {
    return false;
  }
  FDCAN_RxHeaderTypeDef header = {};
  if (HAL_FDCAN_GetRxMessage(hfdcan_, FDCAN_RX_FIFO0, &header, out->data) !=
      HAL_OK) {
    return false;
  }
  out->id = header.Identifier;
  out->extended_id = header.IdType == FDCAN_EXTENDED_ID;
  out->fd = header.FDFormat == FDCAN_FD_CAN;
  out->bitrate_switch = header.BitRateSwitch == FDCAN_BRS_ON;
  out->len = DlcToLen(header.DataLength);
  return true;
}

}  // namespace lhal::stm32

extern "C" void HAL_FDCAN_RxFifo0Callback(FDCAN_HandleTypeDef* hfdcan,
                                          uint32_t interrupts) {
  if ((interrupts & FDCAN_IT_RX_FIFO0_NEW_MESSAGE) != 0) {
    if (auto* c = lhal::stm32::Find(hfdcan)) {
      c->HandleRxFifo0();
    }
  }
}

#endif  // HAL_FDCAN_MODULE_ENABLED
