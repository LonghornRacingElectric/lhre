// Tests for DfuConsole: spotting tools/dfu's "update." request on a console,
// through the UART's RX observer or the shell's byte hook. Scheduler-less.

#include "longhorn/dfu_console.hpp"

#include <atomic>
#include <string>

#include <gtest/gtest.h>

#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"

namespace {

void Inject(lhal::host::Uart& uart, const std::string& bytes) {
  uart.InjectRx(reinterpret_cast<const uint8_t*>(bytes.data()), bytes.size());
}

std::string TakeTx(lhal::host::Uart& uart) {
  auto tx = uart.TakeTx();
  return std::string(tx.begin(), tx.end());
}

struct Board {
  lhal::host::TestClock clock;
  lhal::host::Uart uart;
  longhorn::Shell shell{&uart, &clock, "TestBoard"};
  std::atomic<bool> request{false};
  longhorn::DfuConsole dfu{&request};
};

// The observer path is the one that matters: it fires even while the shell
// task is stalled and never polls.
TEST(DfuConsole, ObserverSeesTheTokenWithoutTheShellPolling) {
  Board b;
  b.dfu.Attach(b.shell, &b.uart);
  Inject(b.uart, "upd");
  EXPECT_FALSE(b.request.load());
  Inject(b.uart, "ate.");
  EXPECT_TRUE(b.request.load());
}

TEST(DfuConsole, NearMissesDoNotRequestTheBootloader) {
  Board b;
  b.dfu.Attach(b.shell, &b.uart);
  Inject(b.uart, "update");
  Inject(b.uart, "\r\n");
  EXPECT_FALSE(b.request.load());
  Inject(b.uart, "updatex.");
  EXPECT_FALSE(b.request.load());
  Inject(b.uart, "UPDATE.");
  EXPECT_FALSE(b.request.load());
  Inject(b.uart, "/update.");
  EXPECT_TRUE(b.request.load());  // "/update." still contains the token
  b.request = false;
  Inject(b.uart, "upd ate.");
  Inject(b.uart, "pdate.");
  EXPECT_FALSE(b.request.load());
}

// A stray 'u' must not eat the start of the real token.
TEST(DfuConsole, RepeatedLeadingCharacterStillMatches) {
  Board b;
  b.dfu.Attach(b.shell, &b.uart);
  Inject(b.uart, "uupdate.");
  EXPECT_TRUE(b.request.load());
}

TEST(DfuConsole, NoStreamFallsBackToTheShellByteHook) {
  Board b;
  b.dfu.Attach(b.shell, nullptr);
  Inject(b.uart, "update.");
  EXPECT_FALSE(b.request.load());  // nothing watches RX until the shell polls
  b.shell.Poll();
  EXPECT_TRUE(b.request.load());
}

// The hook consumes the final byte and clears the shell's line, so the
// token never reaches the command parser as a half-typed line.
TEST(DfuConsole, ByteHookClearsTheLineSoTheNextCommandWorks) {
  Board b;
  b.dfu.AttachByteHook(b.shell);
  Inject(b.uart, "update./uptime\r");
  b.shell.Poll();
  EXPECT_TRUE(b.request.load());
  const std::string out = TakeTx(b.uart);
  EXPECT_EQ(out.find("unknown command"), std::string::npos) << out;
  EXPECT_EQ(out.find("commands start with"), std::string::npos) << out;
}

TEST(DfuConsole, EachConsoleMatchesOnItsOwn) {
  Board a;
  Board b;
  std::atomic<bool> request{false};
  longhorn::DfuConsole dfu_a{&request};
  longhorn::DfuConsole dfu_b{&request};
  dfu_a.Attach(a.shell, &a.uart);
  dfu_b.Attach(b.shell, &b.uart);
  Inject(a.uart, "upda");
  Inject(b.uart, "te.");
  EXPECT_FALSE(request.load());
}

TEST(DfuConsole, PlainLineAcknowledgesOnlyTheWholeToken) {
  lhal::host::Uart uart;
  longhorn::Console out{&uart};
  longhorn::DfuConsole::PlainLine(nullptr, out, "update.");
  EXPECT_NE(TakeTx(uart).find("entering bootloader"), std::string::npos);
  longhorn::DfuConsole::PlainLine(nullptr, out, "update");
  const std::string hint = TakeTx(uart);
  EXPECT_EQ(hint.find("entering bootloader"), std::string::npos);
  EXPECT_NE(hint.find("/help"), std::string::npos);
}

}  // namespace
