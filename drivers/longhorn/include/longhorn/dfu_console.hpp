#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>

#include "lhal/uart.hpp"
#include "longhorn/console.hpp"
#include "longhorn/dfu.hpp"
#include "longhorn/shell.hpp"

namespace longhorn {

// Watches one console for the tools/dfu "update." token and raises
// `request`. Use one per console so partial tokens never join across
// consoles.
class DfuConsole {
 public:
  explicit DfuConsole(std::atomic<bool>* request) : request_(request) {}

  // Prefers the stream's RX observer, which sees the token even while the
  // shell task is stalled. Falls back to the shell's byte hook.
  void Attach(Shell& shell, lhal::Uart* stream) {
    if (stream == nullptr ||
        !stream->SetRxObserver(&DfuConsole::OnReceive, this)) {
      AttachByteHook(shell);
    }
  }

  void AttachByteHook(Shell& shell) {
    shell.SetByteHandler(&DfuConsole::OnByte, this);
  }

  // Shell plain-line handler: acknowledges a whole "update." line.
  static void PlainLine(void* /*context*/, Console& out, const char* line) {
    if (std::strcmp(line, "update.") == 0) {
      out.Println("entering bootloader");
      return;
    }
    out.Println("commands start with '/' (try /help)");
  }

 private:
  static bool OnByte(void* context, uint8_t byte) {
    auto* self = static_cast<DfuConsole*>(context);
    const bool match = self->matcher_.Feed(byte);
    if (match) self->request_->store(true);
    return match;
  }

  static void OnReceive(void* context, const uint8_t* bytes, size_t len) {
    for (size_t i = 0; i < len; ++i) OnByte(context, bytes[i]);
  }

  DfuCommand matcher_;
  std::atomic<bool>* request_;
};

}  // namespace longhorn
