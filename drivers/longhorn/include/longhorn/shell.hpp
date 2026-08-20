#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "FreeRTOS.h"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"
#include "lhre/build_info.hpp"
#include "longhorn/console.hpp"
#include "semphr.h"

namespace longhorn {

// One shell command. Matched against input lines of the form
// "/<name> [args...]"; `args` arrives with leading spaces stripped, or ""
// when the command was given bare.
struct ShellCommand {
  const char* name;  // without the leading '/'
  const char* help;  // one line, shown by /help
  void (*handler)(void* context, Console& out, const char* args);
  void* context;
};

// Line-oriented debug shell over any lhal::Uart byte stream: the debug
// UART a dev board's ST-LINK bridges to the laptop, a USB VCP, or the host
// fake in tests. Typed characters are echoed (with backspace editing), and
// a completed line starting with '/' dispatches a command.
//
// Built in: /help, /version (build provenance from //tools/firmware:
// build_info, same line as PrintBanner), /uptime. Boards register their own
// with AddCommand. tools/monitor parses /version to tell whether the
// flashed firmware matches the working tree.
//
// Thread safety: accepts an optional FreeRTOS mutex to serialize UART TX
// with concurrent loggers (longhorn::Logger) so command outputs and echoes
// never interleave with background logs.
//
// Poll() is non-blocking and single-consumer: call it from one main loop or
// one low-priority task, not from several. A null stream makes the whole
// shell a no-op, so boards without a console keep the wiring in place.
class Shell {
 public:
  static constexpr size_t kMaxLineLength = 64;
  static constexpr size_t kMaxCommands = 16;

  // `board_name` labels the /version banner; keep it a string literal (the
  // pointer is stored, not copied). `clock` may be null: /uptime degrades.
  Shell(lhal::Uart* stream, lhal::Clock* clock, const char* board_name,
        SemaphoreHandle_t mutex = nullptr)
      : stream_(stream),
        clock_(clock),
        board_name_(board_name),
        mutex_(mutex),
        console_(stream) {
    AddCommand({"help", "list commands", &Shell::HelpHandler, this});
    AddCommand({"version", "build banner (board, git describe, sha)",
                &Shell::VersionHandler, this});
    AddCommand(
        {"uptime", "milliseconds since boot", &Shell::UptimeHandler, this});
  }

  // False when the command table is full.
  bool AddCommand(const ShellCommand& command) {
    if (num_commands_ >= kMaxCommands) {
      return false;
    }
    commands_[num_commands_++] = command;
    return true;
  }

  // Drains pending RX bytes, echoing and dispatching completed lines.
  void Poll() {
    if (stream_ == nullptr) {
      return;
    }
    uint8_t byte;
    while (stream_->Read(&byte, 1, 0) == lhal::Status::kOk) {
      HandleByte(static_cast<char>(byte));
    }
  }

  // The one-line build banner: "<board> <describe> (<sha12>[-dirty])".
  // Also what /version prints; boards call it once at boot.
  void PrintBanner() {
    const bool lock = (mutex_ != nullptr &&
                       xTaskGetSchedulerState() == taskSCHEDULER_RUNNING);
    if (lock) {
      xSemaphoreTake(mutex_, portMAX_DELAY);
    }
    PrintBannerUnlocked();
    if (lock) {
      xSemaphoreGive(mutex_);
    }
  }

  // Response/log output stream, shared with the shell so lines interleave
  // whole.
  Console& console() { return console_; }

 private:
  // The mutex is not recursive, so anything that runs while DispatchLine
  // holds it (command handlers) must print through this, never PrintBanner.
  void PrintBannerUnlocked() {
    console_.Printf("%s %s (%.12s%s)", board_name_,
                    lhre::kBuildInfo.git_describe, lhre::kBuildInfo.git_sha,
                    lhre::kBuildInfo.dirty ? "-dirty" : "");
  }

  void HandleByte(char c) {
    if (c == '\r' || c == '\n') {
      if (line_len_ > 0) {
        Echo("\r\n", 2);
        DispatchLine();
      }
      return;
    }
    if (c == '\b' || c == 0x7F) {  // backspace / DEL
      if (line_len_ > 0) {
        --line_len_;
        Echo("\b \b", 3);
      }
      return;
    }
    if (c < 0x20 || c > 0x7E) {  // other control bytes: ignore
      return;
    }
    if (line_len_ < kMaxLineLength - 1) {  // -1 keeps room for the NUL
      line_[line_len_++] = c;
      Echo(&c, 1);
    }
  }

  void DispatchLine() {
    line_[line_len_] = '\0';
    line_len_ = 0;

    const bool lock = (mutex_ != nullptr &&
                       xTaskGetSchedulerState() == taskSCHEDULER_RUNNING);
    if (lock) {
      xSemaphoreTake(mutex_, portMAX_DELAY);
    }

    if (line_[0] != '/') {
      console_.Println("commands start with '/' (try /help)");
      if (lock) {
        xSemaphoreGive(mutex_);
      }
      return;
    }

    const char* name = line_ + 1;
    const char* args = name;
    while (*args != '\0' && *args != ' ') {
      ++args;
    }
    const size_t name_len = static_cast<size_t>(args - name);
    while (*args == ' ') {
      ++args;
    }

    for (size_t i = 0; i < num_commands_; ++i) {
      const ShellCommand& cmd = commands_[i];
      if (std::strncmp(cmd.name, name, name_len) == 0 &&
          cmd.name[name_len] == '\0') {
        cmd.handler(cmd.context, console_, args);
        if (lock) {
          xSemaphoreGive(mutex_);
        }
        return;
      }
    }
    console_.Printf("unknown command /%.*s (try /help)",
                    static_cast<int>(name_len), name);
    if (lock) {
      xSemaphoreGive(mutex_);
    }
  }

  void Echo(const char* bytes, size_t len) {
    const bool lock = (mutex_ != nullptr &&
                       xTaskGetSchedulerState() == taskSCHEDULER_RUNNING);
    if (lock) {
      xSemaphoreTake(mutex_, portMAX_DELAY);
    }
    stream_->Write(reinterpret_cast<const uint8_t*>(bytes), len,
                   /*timeout_ms=*/10);
    if (lock) {
      xSemaphoreGive(mutex_);
    }
  }

  static void HelpHandler(void* context, Console& out, const char* /*args*/) {
    auto* self = static_cast<Shell*>(context);
    for (size_t i = 0; i < self->num_commands_; ++i) {
      out.Printf("/%-10s %s", self->commands_[i].name, self->commands_[i].help);
    }
  }

  static void VersionHandler(void* context, Console& /*out*/,
                             const char* /*args*/) {
    static_cast<Shell*>(context)->PrintBannerUnlocked();
  }

  static void UptimeHandler(void* context, Console& out, const char* /*args*/) {
    auto* self = static_cast<Shell*>(context);
    if (self->clock_ == nullptr) {
      out.Println("uptime unavailable (no clock)");
      return;
    }
    out.Printf("uptime %lu ms",
               static_cast<unsigned long>(self->clock_->Millis()));
  }

  lhal::Uart* stream_;
  lhal::Clock* clock_;
  const char* board_name_;
  SemaphoreHandle_t mutex_;
  Console console_;

  ShellCommand commands_[kMaxCommands];
  size_t num_commands_ = 0;

  char line_[kMaxLineLength];
  size_t line_len_ = 0;
};

}  // namespace longhorn
