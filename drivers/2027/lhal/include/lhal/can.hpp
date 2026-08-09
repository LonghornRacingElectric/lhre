#pragma once

#include <cstddef>
#include <cstdint>

#include "lhal/status.hpp"

namespace lhal {

struct CanFrame {
  static constexpr size_t kMaxData = 64;  // CAN FD; classic CAN uses <= 8

  uint32_t id = 0;
  bool extended_id = false;
  bool fd = false;              // CAN FD frame (len may exceed 8)
  bool bitrate_switch = false;  // CAN FD bit-rate switching
  uint8_t len = 0;
  uint8_t data[kMaxData] = {};
};

class CanBus {
 public:
  // May be invoked from interrupt context on embedded targets.
  using RxCallback = void (*)(void* context, const CanFrame& frame);

  virtual ~CanBus() = default;

  // Queue a frame for transmission. Returns kBusy if the TX queue is full.
  virtual Status Send(const CanFrame& frame) = 0;

  // Non-blocking poll of the receive queue. Returns false if empty. Frames
  // consumed by the RX callback (if one is set) do not show up here.
  virtual bool Receive(CanFrame* out) = 0;

  // Called for every received frame. Pass nullptr to revert to polling.
  virtual void SetRxCallback(RxCallback callback, void* context) = 0;
};

}  // namespace lhal
