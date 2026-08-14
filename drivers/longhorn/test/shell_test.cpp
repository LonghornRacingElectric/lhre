// Tests for the longhorn shell, run against the LHAL host backend.

#include <string>

#include <gtest/gtest.h>

#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "lhre/build_info.hpp"
#include "longhorn/shell.hpp"

namespace {

std::string TakeTxString(lhal::host::Uart& uart) {
  auto tx = uart.TakeTx();
  return std::string(tx.begin(), tx.end());
}

void Type(lhal::host::Uart& uart, longhorn::Shell& shell,
          const std::string& bytes) {
  uart.InjectRx(reinterpret_cast<const uint8_t*>(bytes.data()), bytes.size());
  shell.Poll();
}

bool Contains(const std::string& haystack, const std::string& needle) {
  return haystack.find(needle) != std::string::npos;
}

struct ShellFixture {
  lhal::host::TestClock clock;
  lhal::host::Uart uart;
  longhorn::Shell shell{&uart, &clock, "TestBoard"};
};

TEST(Shell, VersionPrintsBanner) {
  ShellFixture f;
  Type(f.uart, f.shell, "/version\r");

  const std::string out = TakeTxString(f.uart);
  EXPECT_TRUE(Contains(out, "TestBoard")) << out;
  EXPECT_TRUE(Contains(out, lhre::kBuildInfo.git_describe)) << out;
  // The banner truncates the sha to 12 characters.
  EXPECT_TRUE(
      Contains(out, std::string(lhre::kBuildInfo.git_sha).substr(0, 12)))
      << out;
}

TEST(Shell, EchoesTypedCharacters) {
  ShellFixture f;
  Type(f.uart, f.shell, "/ver");
  EXPECT_EQ(TakeTxString(f.uart), "/ver");
}

TEST(Shell, HelpListsBuiltinsAndRegistered) {
  ShellFixture f;
  f.shell.AddCommand({"blinks", "blink count",
                      [](void*, longhorn::Console&, const char*) {}, nullptr});
  Type(f.uart, f.shell, "/help\r");

  const std::string out = TakeTxString(f.uart);
  EXPECT_TRUE(Contains(out, "/help")) << out;
  EXPECT_TRUE(Contains(out, "/version")) << out;
  EXPECT_TRUE(Contains(out, "/uptime")) << out;
  EXPECT_TRUE(Contains(out, "/blinks")) << out;
}

TEST(Shell, UptimeReadsClock) {
  ShellFixture f;
  f.clock.Advance(1234);
  Type(f.uart, f.shell, "/uptime\r");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "uptime 1234 ms"));
}

TEST(Shell, CustomCommandReceivesTrimmedArgs) {
  ShellFixture f;
  static std::string got_args;
  got_args.clear();
  f.shell.AddCommand({"echo", "test",
                      [](void*, longhorn::Console& out, const char* args) {
                        got_args = args;
                        out.Println("echoed");
                      },
                      nullptr});

  Type(f.uart, f.shell, "/echo   hello world\r");
  EXPECT_EQ(got_args, "hello world");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "echoed"));

  Type(f.uart, f.shell, "/echo\r");
  EXPECT_EQ(got_args, "");
}

TEST(Shell, UnknownCommandNamesItself) {
  ShellFixture f;
  Type(f.uart, f.shell, "/nope 1 2\r");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "unknown command /nope"));
}

TEST(Shell, PrefixOfACommandDoesNotMatch) {
  ShellFixture f;
  Type(f.uart, f.shell, "/ver\r");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "unknown command /ver"));
}

TEST(Shell, NonSlashLineGetsHint) {
  ShellFixture f;
  Type(f.uart, f.shell, "hello\r");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "commands start with '/'"));
}

TEST(Shell, BackspaceEditsLine) {
  ShellFixture f;
  Type(f.uart, f.shell, "/versoin\x7f\x7f\x7fion\r");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "TestBoard"));
}

TEST(Shell, CrlfDispatchesOnce) {
  ShellFixture f;
  static int calls = 0;
  calls = 0;
  f.shell.AddCommand({"count", "test",
                      [](void*, longhorn::Console&, const char*) { ++calls; },
                      nullptr});
  Type(f.uart, f.shell, "/count\r\n");
  EXPECT_EQ(calls, 1);
}

TEST(Shell, OverlongLineIsTruncatedNotCrashed) {
  ShellFixture f;
  Type(f.uart, f.shell, "/" + std::string(300, 'x') + "\r");
  EXPECT_TRUE(Contains(TakeTxString(f.uart), "unknown command"));
}

TEST(Shell, CommandTableCapacity) {
  ShellFixture f;
  auto noop = [](void*, longhorn::Console&, const char*) {};
  // Three built-ins are pre-registered.
  for (size_t i = 3; i < longhorn::Shell::kMaxCommands; ++i) {
    EXPECT_TRUE(f.shell.AddCommand({"x", "x", noop, nullptr}));
  }
  EXPECT_FALSE(f.shell.AddCommand({"x", "x", noop, nullptr}));
}

TEST(Shell, NullStreamIsNoop) {
  lhal::host::TestClock clock;
  longhorn::Shell shell(nullptr, &clock, "TestBoard");
  shell.Poll();  // must not crash
  shell.PrintBanner();
}

}  // namespace
