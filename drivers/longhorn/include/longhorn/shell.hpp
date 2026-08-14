#pragma once

#include <cstddef>
#include <cstdint>

#include "lhal/system.hpp"
#include "lhal/uart.hpp"
#include "longhorn/console.hpp"

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
// Poll() is non-blocking and single-consumer: call it from one main loop or
// one low-priority task, not from several. A null stream makes the whole
// shell a no-op, so boards without a console keep the wiring in place.
class Shell {
 public:
  static constexpr size_t kMaxLineLength = 64;
  static constexpr size_t kMaxCommands = 16;

  // `board_name` labels the /version banner; keep it a string literal (the
  // pointer is stored, not copied). `clock` may be null: /uptime degrades.
  Shell(lhal::Uart* stream, lhal::Clock* clock, const char* board_name);

  // False when the command table is full.
  bool AddCommand(const ShellCommand& command);

  // Drains pending RX bytes, echoing and dispatching completed lines.
  void Poll();

  // The one-line build banner: "<board> <describe> (<sha12>[-dirty])".
  // Also what /version prints; boards call it once at boot.
  void PrintBanner();

  // Response/log output stream, shared with the shell so lines interleave
  // whole. Boards log through this instead of owning a second Console.
  Console& console() { return console_; }

 private:
  void HandleByte(char c);
  void DispatchLine();
  void Echo(const char* bytes, size_t len);

  static void HelpHandler(void* context, Console& out, const char* args);
  static void VersionHandler(void* context, Console& out, const char* args);
  static void UptimeHandler(void* context, Console& out, const char* args);

  lhal::Uart* stream_;
  lhal::Clock* clock_;
  const char* board_name_;
  Console console_;

  ShellCommand commands_[kMaxCommands];
  size_t num_commands_ = 0;

  char line_[kMaxLineLength];
  size_t line_len_ = 0;
};

}  // namespace longhorn
