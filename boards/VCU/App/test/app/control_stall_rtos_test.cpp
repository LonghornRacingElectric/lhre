// The supervisor stops feeding the watchdog when the control task stalls, so
// the IWDG resets the board. Runs the real tasks under the FreeRTOS
// simulator: Step() runs every tick inline and cannot stall one.

#include <gtest/gtest.h>

#include "test/rtos_stall.hpp"

namespace {

TEST(ControlStallRtos, WatchdogIsNotFedOnceTheControlTaskStalls) {
  static lhal::host::CanNetwork network;
  static lhal::host::Can data(&network);
  static vcu::test::StuckCan critical;  // the control task sends here

  const vcu::test::WatchdogFeeds feeds =
      vcu::test::RunTasksWith(&critical, &data);

  EXPECT_EQ(feeds.at_end, feeds.after_deadlines)
      << "the supervisor still fed the watchdog with the control task stuck";
}

}  // namespace
