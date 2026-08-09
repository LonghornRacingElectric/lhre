// Host unit tests for the VCU application logic, using LHAL host fakes.

#include <gtest/gtest.h>

#include "lhal/host/can.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "vcu_app.hpp"

namespace {

TEST(VcuApp, BlinksAndSendsHeartbeats) {
  lhal::host::TestClock clock;
  lhal::host::Gpio led;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can(&network);
  lhal::host::Can dash(&network);  // observer node, e.g. the dashboard

  vcu::Peripherals p;
  p.clock = &clock;
  p.status_led = &led;
  p.can = &vcu_can;
  vcu::App app(p);

  // Simulate one second in 10 ms main-loop ticks.
  for (int i = 0; i < 100; ++i) {
    app.Step();
    clock.Advance(10);
  }
  app.Step();

  // 500 ms blink period over 1 s: LED toggled at t=0, 500, 1000 → on.
  EXPECT_TRUE(led.Read());

  // 100 ms heartbeat period over 1 s: t=0..1000 inclusive.
  EXPECT_EQ(app.heartbeats_sent(), 11u);

  // The observer node saw every heartbeat, with a counting payload.
  lhal::CanFrame frame;
  uint32_t received = 0;
  while (dash.Receive(&frame)) {
    EXPECT_EQ(frame.id, vcu::App::kHeartbeatCanId);
    ASSERT_EQ(frame.len, 4u);
    EXPECT_EQ(frame.data[0], received);  // counter low byte
    ++received;
  }
  EXPECT_EQ(received, 11u);
}

TEST(VcuApp, RunsWithoutOptionalPeripherals) {
  lhal::host::TestClock clock;
  vcu::Peripherals p;
  p.clock = &clock;
  vcu::App app(p);

  for (int i = 0; i < 100; ++i) {
    app.Step();
    clock.Advance(10);
  }
  EXPECT_EQ(app.heartbeats_sent(), 0u);
}

}  // namespace
