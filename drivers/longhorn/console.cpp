#include "longhorn/console.hpp"

#include <cstdio>
#include <cstring>

namespace longhorn {

Console::Console(lhal::Uart* stream, uint32_t write_timeout_ms)
    : stream_(stream), write_timeout_ms_(write_timeout_ms) {}

void Console::Println(const char* message) {
  if (stream_ == nullptr) {
    return;
  }
  size_t len = std::strlen(message);
  if (len > kBufferSize - 2) {
    len = kBufferSize - 2;
  }
  std::memcpy(buffer_, message, len);
  Send(len);
}

void Console::VPrintf(const char* format, va_list args) {
  if (stream_ == nullptr) {
    return;
  }
  // Leave two bytes for the CRLF; vsnprintf's count includes its NUL.
  int len = std::vsnprintf(buffer_, kBufferSize - 2, format, args);
  if (len < 0) {
    return;
  }
  // On truncation vsnprintf reports the untruncated length.
  if (len > static_cast<int>(kBufferSize - 3)) {
    len = kBufferSize - 3;
  }
  Send(static_cast<size_t>(len));
}

void Console::Printf(const char* format, ...) {
  va_list args;
  va_start(args, format);
  VPrintf(format, args);
  va_end(args);
}

void Console::Send(size_t len) {
  buffer_[len] = '\r';
  buffer_[len + 1] = '\n';
  stream_->Write(reinterpret_cast<const uint8_t*>(buffer_), len + 2,
                 write_timeout_ms_);
}

}  // namespace longhorn
