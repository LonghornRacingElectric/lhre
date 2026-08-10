// Tests for the longhorn console, run against the LHAL host backend.

#include <string>

#include <gtest/gtest.h>

#include "lhal/host/uart.hpp"
#include "longhorn/console.hpp"

namespace {

std::string TakeTxString(lhal::host::Uart& uart) {
  auto tx = uart.TakeTx();
  return std::string(tx.begin(), tx.end());
}

TEST(Console, PrintlnAppendsCrlf) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  console.Println("hello");
  EXPECT_EQ(TakeTxString(uart), "hello\r\n");
}

TEST(Console, PrintfFormats) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  console.Printf("cell %d at %.2f V", 3, 3.71);
  EXPECT_EQ(TakeTxString(uart), "cell 3 at 3.71 V\r\n");
}

TEST(Console, PrintlnTruncatesLongMessage) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  std::string longmsg(300, 'a');
  console.Println(longmsg.c_str());

  std::string sent = TakeTxString(uart);
  EXPECT_EQ(sent.size(), longhorn::Console::kBufferSize);
  EXPECT_EQ(sent.substr(sent.size() - 2), "\r\n");
  EXPECT_EQ(sent.substr(0, sent.size() - 2),
            longmsg.substr(0, longhorn::Console::kBufferSize - 2));
}

TEST(Console, PrintfTruncatesLongMessage) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  std::string longmsg(300, 'b');
  console.Printf("%s", longmsg.c_str());

  std::string sent = TakeTxString(uart);
  // One byte shorter than Println's cap: vsnprintf also stores its NUL.
  EXPECT_EQ(sent.size(), longhorn::Console::kBufferSize - 1);
  EXPECT_EQ(sent.substr(sent.size() - 2), "\r\n");
}

TEST(Console, NullStreamIsNoOp) {
  longhorn::Console console(nullptr);
  console.Println("dropped");
  console.Printf("%d", 42);  // must not crash
}

TEST(Console, ConsecutiveCallsEachGetOwnLine) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  console.Println("one");
  console.Printf("two %c", '!');
  EXPECT_EQ(TakeTxString(uart), "one\r\ntwo !\r\n");
}

}  // namespace
