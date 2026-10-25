#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "lhal/can.hpp"
#include "lhal/system.hpp"

namespace vcu {

// ISR-to-control SPSC queue for classic CAN frames.
class CanInbox {
 public:
  static constexpr size_t kDepth = 64;  // power of two

  void Attach(lhal::CanBus* bus) {
    if (bus != nullptr) {
      bus->SetRxCallback(&CanInbox::OnFrame, this);
    }
  }

  // Consumer side. Returns false when empty.
  bool Pop(lhal::CanFrame* out) {
    const size_t tail = tail_.load(std::memory_order_relaxed);
    if (tail == head_.load(std::memory_order_acquire)) {
      return false;
    }
    const Slot& s = slots_[tail % kDepth];
    *out = lhal::CanFrame{};
    out->id = s.id;
    out->extended_id = s.extended;
    out->len = s.len;
    std::memcpy(out->data, s.data, s.len);
    tail_.store(tail + 1, std::memory_order_release);
    return true;
  }

  uint32_t dropped() const { return dropped_.load(std::memory_order_relaxed); }

 private:
  struct Slot {
    uint32_t id;
    bool extended;
    uint8_t len;
    uint8_t data[8];
  };

  static void OnFrame(void* context, const lhal::CanFrame& frame) {
    static_cast<CanInbox*>(context)->Push(frame);
  }

  void Push(const lhal::CanFrame& frame) {
    const size_t head = head_.load(std::memory_order_relaxed);
    if (frame.len > 8 ||
        head - tail_.load(std::memory_order_acquire) >= kDepth) {
      dropped_.fetch_add(1, std::memory_order_relaxed);
      return;
    }
    Slot& s = slots_[head % kDepth];
    s.id = frame.id;
    s.extended = frame.extended_id;
    s.len = frame.len;
    std::memcpy(s.data, frame.data, frame.len);
    head_.store(head + 1, std::memory_order_release);
  }

  Slot slots_[kDepth] = {};
  std::atomic<size_t> head_{0};
  std::atomic<size_t> tail_{0};
  std::atomic<uint32_t> dropped_{0};
};

// The latest copy of one received message with its arrival time.
template <class Message>
struct RxSlot {
  // Twice the spec period (lhre-2026 generate_can_lib.py:181-183), rounded
  // up; 0 for aperiodic messages (never time out, never fresh by age).
  static constexpr uint32_t kTimeoutMs =
      Message::kFrequencyHz > 0.0f
          ? static_cast<uint32_t>(2000.0f / Message::kFrequencyHz + 0.999f)
          : 0;

  Message msg{};
  uint32_t last_ms = 0;
  uint32_t count = 0;

  bool seen() const { return count != 0; }
  bool Fresh(uint32_t now_ms) const {
    return seen() && kTimeoutMs != 0 &&
           !lhal::ElapsedMs(now_ms, last_ms, kTimeoutMs);
  }

  // Decodes `frame` into this slot if it is this message. Standard IDs
  // only; short frames are ignored.
  bool TryDecode(const lhal::CanFrame& frame, uint32_t now_ms) {
    if (frame.extended_id || !Message::Matches(frame.id) ||
        frame.len < Message::kDlc) {
      return false;
    }
    msg = Message::FromFrame(frame);
    last_ms = now_ms;
    ++count;
    return true;
  }
};

}  // namespace vcu
