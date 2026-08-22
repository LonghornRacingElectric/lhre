// Tests for the longhorn logger, run against the LHAL host backend and the
// FreeRTOS simulator port.
//
// The Logger suite is scheduler-less: queue operations with zero waits work
// before vTaskStartScheduler(), so formatting, dropping, and draining are
// tested deterministically with a TestClock. The LoggerRtos suite then runs
// the real drain task under the scheduler — it must stay the last test in
// this binary, and the only one that starts the scheduler (the kernel's
// static state isn't reusable after vTaskEndScheduler()).

#include <string>

#include <gtest/gtest.h>

#include "FreeRTOS.h"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "longhorn/logger.hpp"
#include "task.h"

namespace {

std::string TakeTxString(lhal::host::Uart& uart) {
  auto tx = uart.TakeTx();
  return std::string(tx.begin(), tx.end());
}

TEST(Logger, FormatsTimestampLevelTagAndBody) {
  lhal::host::Uart uart;
  lhal::host::TestClock clock;
  longhorn::Logger logger(&uart, &clock);

  clock.Advance(42);
  logger.Info("hello %s", "world");
  EXPECT_TRUE(logger.DrainOne(0));
  EXPECT_EQ(TakeTxString(uart), "[42] \x1b[0;34m[INFO]\x1b[0m hello world\r\n");
}

TEST(Logger, LevelsGetDistinctTags) {
  lhal::host::Uart uart;
  lhal::host::TestClock clock;
  longhorn::Logger logger(&uart, &clock);

  logger.Success("s");
  logger.Warning("w");
  logger.Error("e");
  while (logger.DrainOne(0)) {
  }
  std::string out = TakeTxString(uart);
  EXPECT_NE(out.find("[SUCCESS]"), std::string::npos);
  EXPECT_NE(out.find("[WARNING]"), std::string::npos);
  EXPECT_NE(out.find("[ERROR]"), std::string::npos);
}

TEST(Logger, DropsWhenQueueFullAndCounts) {
  lhal::host::Uart uart;
  lhal::host::TestClock clock;
  longhorn::Logger logger(&uart, &clock);

  for (int i = 0; i < static_cast<int>(longhorn::Logger::kQueueLength) + 2;
       ++i) {
    logger.Info("msg %d", i);
  }
  EXPECT_EQ(logger.dropped(), 2u);

  int drained = 0;
  while (logger.DrainOne(0)) {
    ++drained;
  }
  EXPECT_EQ(drained, static_cast<int>(longhorn::Logger::kQueueLength));

  // The dropped lines are the newest ones — the last kept message is msg 7.
  std::string out = TakeTxString(uart);
  EXPECT_NE(out.find("msg 7\r\n"), std::string::npos);
  EXPECT_EQ(out.find("msg 8"), std::string::npos);
}

TEST(Logger, TruncatesLongLineButKeepsCrlf) {
  lhal::host::Uart uart;
  lhal::host::TestClock clock;
  longhorn::Logger logger(&uart, &clock);

  std::string longmsg(300, 'x');
  logger.Info("%s", longmsg.c_str());
  EXPECT_TRUE(logger.DrainOne(0));

  std::string out = TakeTxString(uart);
  EXPECT_EQ(out.size(), longhorn::Logger::kMessageSize - 1);
  EXPECT_EQ(out.substr(out.size() - 2), "\r\n");
}

TEST(Logger, NullStreamIsNoOp) {
  lhal::host::TestClock clock;
  longhorn::Logger logger(nullptr, &clock);
  logger.Info("dropped");
  EXPECT_FALSE(logger.DrainOne(0));
  EXPECT_EQ(logger.dropped(), 0u);
}

TEST(Logger, DisconnectedStreamIsNoOp) {
  lhal::host::Uart uart;
  lhal::host::TestClock clock;
  longhorn::Logger logger(&uart, &clock);

  uart.set_connected(false);
  EXPECT_FALSE(logger.connected());

  logger.Info("dropped without queuing");
  EXPECT_FALSE(logger.DrainOne(0));
  EXPECT_EQ(logger.dropped(), 0u);
  EXPECT_TRUE(uart.TakeTx().empty());
}

// --- Scheduler run: keep last, one per process. ---

void ProducerEntry(void* arg) {
  auto* logger = static_cast<longhorn::Logger*>(arg);
  logger->Info("boot complete");
  logger->Warning("cell %d low", 3);
  logger->Error("overcurrent on %s", "FL");
  // Let the low-priority drain task run, then stop everything.
  vTaskDelay(pdMS_TO_TICKS(100));
  vTaskEndScheduler();
}

TEST(LoggerRtos, DrainTaskWritesQueuedLinesUnderScheduler) {
  static lhal::host::Uart uart;
  static lhal::host::SystemClock clock;
  static longhorn::Logger logger(&uart, &clock);

  ASSERT_NE(logger.StartTask(), nullptr);

  static StaticTask_t producer_tcb;
  static StackType_t producer_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(ProducerEntry, "producer", configMINIMAL_STACK_SIZE,
                    &logger, configMAX_PRIORITIES - 1, producer_stack,
                    &producer_tcb);

  vTaskStartScheduler();  // returns when ProducerEntry ends the scheduler

  std::string out = TakeTxString(uart);
  int lines = 0;
  for (size_t pos = out.find("\r\n"); pos != std::string::npos;
       pos = out.find("\r\n", pos + 2)) {
    ++lines;
  }
  EXPECT_EQ(lines, 3);
  EXPECT_NE(out.find("boot complete"), std::string::npos);
  EXPECT_NE(out.find("cell 3 low"), std::string::npos);
  EXPECT_NE(out.find("overcurrent on FL"), std::string::npos);
  EXPECT_EQ(logger.dropped(), 0u);
}

}  // namespace
