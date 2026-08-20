#pragma once

#include <cstdarg>
#include <cstddef>
#include <cstdint>

#include "lhal/uart.hpp"

namespace longhorn {

// printf-style text output over any lhal::Uart byte stream — a debug UART,
// a USB virtual COM port (lhal::stm32::UsbCdc), or the host fake in tests.
// Every call sends one line with "\r\n" appended; messages longer than the
// internal buffer are truncated.
//
// A null stream or a disconnected stream (USB port not opened by host) makes
// every call a silent, zero-overhead no-op, so boards can keep output calls
// in place without stalling or wasting cycles.
//
// Lines are sent raw and un-prefixed, ideal for command-line shells (/version,
// /state, /help) and human/machine-readable text. For timestamped,
// level-tagged, thread-safe logging from RTOS tasks, use longhorn::Logger.
//
// Not thread-safe: the format buffer is shared across calls. Serialize calls
// behind a mutex (or use Logger) when printing from multiple RTOS tasks.
class Console {
 public:
  static constexpr size_t kBufferSize = 256;

  explicit Console(lhal::Uart* stream, uint32_t write_timeout_ms = 100);

  void Println(const char* message);
  void Printf(const char* format, ...) __attribute__((format(printf, 2, 3)));
  void VPrintf(const char* format, va_list args);

  // True when the transport is connected and receiving data.
  bool connected() const { return stream_ != nullptr && stream_->connected(); }

 private:
  void Send(size_t len);

  lhal::Uart* stream_;
  uint32_t write_timeout_ms_;
  char buffer_[kBufferSize];
};

}  // namespace longhorn
