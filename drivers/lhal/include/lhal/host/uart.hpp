#pragma once

#include <cstring>
#include <deque>
#include <vector>

#include "lhal/uart.hpp"

namespace lhal::host {

// In-memory UART for tests and sims. Writes accumulate in tx_data(); feed
// the receive side with InjectRx(). Async transfers complete immediately.
class Uart final : public lhal::Uart {
 public:
  Status Write(const uint8_t* data, size_t len,
               uint32_t /*timeout_ms*/) override {
    tx_.insert(tx_.end(), data, data + len);
    return Status::kOk;
  }

  // Copies up to `len` bytes from the injected RX stream. Mirrors the HAL:
  // returns kTimeout when fewer than `len` bytes were available (the
  // available bytes are still consumed and copied).
  Status Read(uint8_t* data, size_t len, uint32_t /*timeout_ms*/) override {
    size_t n = 0;
    while (n < len && !rx_.empty()) {
      data[n++] = rx_.front();
      rx_.pop_front();
    }
    return n == len ? Status::kOk : Status::kTimeout;
  }

  Status WriteAsync(const uint8_t* data, size_t len, CompletionCallback done,
                    void* context) override {
    Status s = Write(data, len, 0);
    if (done != nullptr) {
      done(context, s);
    }
    return Status::kOk;
  }

  Status ReadAsync(uint8_t* data, size_t len, CompletionCallback done,
                   void* context) override {
    Status s = Read(data, len, 0);
    if (done != nullptr) {
      done(context, s);
    }
    return Status::kOk;
  }

  // Test helpers -----------------------------------------------------------
  bool connected() const override { return connected_; }
  void set_connected(bool connected) { connected_ = connected; }

  void InjectRx(const uint8_t* data, size_t len) {
    rx_.insert(rx_.end(), data, data + len);
  }
  const std::vector<uint8_t>& tx_data() const { return tx_; }
  std::vector<uint8_t> TakeTx() {
    std::vector<uint8_t> out;
    out.swap(tx_);
    return out;
  }

 private:
  bool connected_ = true;
  std::vector<uint8_t> tx_;
  std::deque<uint8_t> rx_;
};

}  // namespace lhal::host
