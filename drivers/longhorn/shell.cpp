#include "longhorn/shell.hpp"

#include <cstring>

#include "lhre/build_info.hpp"

namespace longhorn {

Shell::Shell(lhal::Uart* stream, lhal::Clock* clock, const char* board_name)
    : stream_(stream),
      clock_(clock),
      board_name_(board_name),
      console_(stream) {
  AddCommand({"help", "list commands", &Shell::HelpHandler, this});
  AddCommand({"version", "build banner (board, git describe, sha)",
              &Shell::VersionHandler, this});
  AddCommand(
      {"uptime", "milliseconds since boot", &Shell::UptimeHandler, this});
}

bool Shell::AddCommand(const ShellCommand& command) {
  if (num_commands_ >= kMaxCommands) {
    return false;
  }
  commands_[num_commands_++] = command;
  return true;
}

void Shell::Poll() {
  if (stream_ == nullptr) {
    return;
  }
  // One byte at a time with timeout 0: kOk exactly when a byte was already
  // waiting, on the STM32 HAL and the host fake alike.
  uint8_t byte;
  while (stream_->Read(&byte, 1, 0) == lhal::Status::kOk) {
    HandleByte(static_cast<char>(byte));
  }
}

void Shell::PrintBanner() {
  console_.Printf("%s %s (%.12s%s)", board_name_, lhre::kBuildInfo.git_describe,
                  lhre::kBuildInfo.git_sha,
                  lhre::kBuildInfo.dirty ? "-dirty" : "");
}

void Shell::HandleByte(char c) {
  if (c == '\r' || c == '\n') {
    // Only a non-empty line gets the echoed newline, so a CRLF-sending
    // terminal doesn't dispatch (or scroll) twice per enter.
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

void Shell::DispatchLine() {
  line_[line_len_] = '\0';
  line_len_ = 0;

  if (line_[0] != '/') {
    console_.Println("commands start with '/' (try /help)");
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
      return;
    }
  }
  console_.Printf("unknown command /%.*s (try /help)",
                  static_cast<int>(name_len), name);
}

void Shell::Echo(const char* bytes, size_t len) {
  stream_->Write(reinterpret_cast<const uint8_t*>(bytes), len,
                 /*timeout_ms=*/10);
}

void Shell::HelpHandler(void* context, Console& out, const char* /*args*/) {
  auto* self = static_cast<Shell*>(context);
  for (size_t i = 0; i < self->num_commands_; ++i) {
    out.Printf("/%-10s %s", self->commands_[i].name, self->commands_[i].help);
  }
}

void Shell::VersionHandler(void* context, Console& /*out*/,
                           const char* /*args*/) {
  static_cast<Shell*>(context)->PrintBanner();
}

void Shell::UptimeHandler(void* context, Console& out, const char* /*args*/) {
  auto* self = static_cast<Shell*>(context);
  if (self->clock_ == nullptr) {
    out.Println("uptime unavailable (no clock)");
    return;
  }
  out.Printf("uptime %lu ms",
             static_cast<unsigned long>(self->clock_->Millis()));
}

}  // namespace longhorn
