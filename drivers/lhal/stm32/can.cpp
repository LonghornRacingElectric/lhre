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

// Interrupts off for the duration of a scope; restores the previous state
// so it nests inside ISRs and other critical sections.
class IrqGuard {
 public:
  IrqGuard() : primask_(__get_PRIMASK()) { __disable_irq(); }
  ~IrqGuard() { __set_PRIMASK(primask_); }
  IrqGuard(const IrqGuard&) = delete;
  IrqGuard& operator=(const IrqGuard&) = delete;

 private:
  uint32_t primask_;
};

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
      hfdcan_,
      FDCAN_IT_RX_FIFO0_NEW_MESSAGE | FDCAN_IT_RX_FIFO0_MESSAGE_LOST |
          FDCAN_IT_TX_FIFO_EMPTY | FDCAN_IT_BUS_OFF,
      0));
}

Status Can::Send(const CanFrame& frame) {
  IrqGuard guard;
  // Queue behind anything already waiting so frames leave in order.
  if (tx_count_ == 0 && AddToHardware(frame)) {
    return Status::kOk;
  }
  if (tx_count_ == kTxQueueDepth) {
    tx_dropped_ = tx_dropped_ + 1;
    return Status::kBusy;
  }
  tx_queue_[(tx_head_ + tx_count_) % kTxQueueDepth] = frame;
  ++tx_count_;
  DrainTxQueue();
  return Status::kOk;
}

void Can::DrainTxQueue() {
  while (tx_count_ > 0 && AddToHardware(tx_queue_[tx_head_])) {
    tx_head_ = (tx_head_ + 1) % kTxQueueDepth;
    --tx_count_;
  }
}

bool Can::AddToHardware(const CanFrame& frame) {
  if (HAL_FDCAN_GetTxFifoFreeLevel(hfdcan_) == 0) {
    return false;
  }
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
  return HAL_FDCAN_AddMessageToTxFifoQ(
             hfdcan_, &header, const_cast<uint8_t*>(frame.data)) == HAL_OK;
}

bool Can::Receive(CanFrame* out) { return ReadFifo0(out); }

void Can::SetRxCallback(RxCallback callback, void* context) {
  rx_callback_ = callback;
  rx_context_ = context;
}

CanStats Can::stats() const {
  CanStats out;
  out.bus_off_count = bus_off_count_;
  out.tx_dropped = tx_dropped_;
  out.rx_lost = rx_lost_;
  FDCAN_ErrorCountersTypeDef counters = {};
  if (HAL_FDCAN_GetErrorCounters(hfdcan_, &counters) == HAL_OK) {
    out.tx_error_count = static_cast<uint8_t>(counters.TxErrorCnt);
    out.rx_error_count = static_cast<uint8_t>(counters.RxErrorCnt);
  }
  out.bus_off = (hfdcan_->Instance->PSR & FDCAN_PSR_BO) != 0;
  out.error_passive = (hfdcan_->Instance->PSR & FDCAN_PSR_EP) != 0;
  return out;
}

void Can::HandleRxFifo0Lost() { rx_lost_ = rx_lost_ + 1; }

void Can::HandleTxFifoEmpty() {
  IrqGuard guard;
  DrainTxQueue();
}

void Can::HandleBusOff() {
  bus_off_count_ = bus_off_count_ + 1;
  // Bus-off sets CCCR.INIT; clearing it starts the recovery sequence (the
  // controller rejoins after 128 occurrences of 11 recessive bits).
  CLEAR_BIT(hfdcan_->Instance->CCCR, FDCAN_CCCR_INIT);
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
  auto* c = lhal::stm32::Find(hfdcan);
  if (c == nullptr) {
    return;
  }
  if ((interrupts & FDCAN_IT_RX_FIFO0_MESSAGE_LOST) != 0) {
    c->HandleRxFifo0Lost();
  }
  if ((interrupts & FDCAN_IT_RX_FIFO0_NEW_MESSAGE) != 0) {
    c->HandleRxFifo0();
  }
}

extern "C" void HAL_FDCAN_TxFifoEmptyCallback(FDCAN_HandleTypeDef* hfdcan) {
  if (auto* c = lhal::stm32::Find(hfdcan)) {
    c->HandleTxFifoEmpty();
  }
}

extern "C" void HAL_FDCAN_ErrorStatusCallback(FDCAN_HandleTypeDef* hfdcan,
                                              uint32_t interrupts) {
  if ((interrupts & FDCAN_IT_BUS_OFF) != 0) {
    if (auto* c = lhal::stm32::Find(hfdcan)) {
      c->HandleBusOff();
    }
  }
}

#endif  // HAL_FDCAN_MODULE_ENABLED
