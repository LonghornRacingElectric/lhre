// The supervisor stops feeding the watchdog when the telemetry task stalls.
// See control_stall_rtos_test.cpp for why this runs under the simulator.

#include <gtest/gtest.h>

#include "test/rtos_stall.hpp"

namespace {

TEST(TelemetryStallRtos, WatchdogIsNotFedOnceTheTelemetryTaskStalls) {
  static lhal::host::CanNetwork network;
  static lhal::host::Can critical(&network);
  static vcu::test::StuckCan data;  // the telemetry task sends here

  const vcu::test::WatchdogFeeds feeds =
      vcu::test::RunTasksWith(&critical, &data);

  // The control task still runs, so the feeds go on until the telemetry
  // deadline: a lower bound, as in vcu_rtos_test.
  EXPECT_GE(feeds.after_deadlines, 5u);
  EXPECT_EQ(feeds.at_end, feeds.after_deadlines)
      << "the supervisor still fed the watchdog with the telemetry task stuck";
}

}  // namespace
