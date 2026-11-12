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

// Health counters for one bus. Backends that can't observe a counter leave
// it zero.
struct CanStats {
  uint32_t bus_off_count = 0;  // bus-off events (each followed by recovery)
  uint32_t tx_dropped = 0;     // Send() calls refused because queues were full
  uint32_t rx_lost = 0;        // frames lost to RX FIFO overrun
  uint8_t tx_error_count = 0;  // CAN TEC
  uint8_t rx_error_count = 0;  // CAN REC
  bool bus_off = false;        // currently bus-off
  bool error_passive = false;
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

  virtual CanStats stats() const { return {}; }
};

}  // namespace lhal
