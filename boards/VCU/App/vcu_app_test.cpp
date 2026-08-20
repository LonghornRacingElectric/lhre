// Host unit tests for the VCU application logic, using LHAL host fakes.

#include <gtest/gtest.h>

#include <string>

#include "lhal/host/can.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_vcu.hpp"
#include "vcu_app.hpp"

namespace {

using lhre::can::VcuState;
using lhre::can::hvc::HvcPackStatus;
using lhre::can::vcu::VcuStatus;

TEST(VcuApp, BlinksAndBroadcastsStatus) {
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

  // 100 ms blink period over 1 s: toggled at t=0, 100, ..., 1000 → 11
  // toggles → on.
  EXPECT_TRUE(led.Read());

  // 100 ms status period over 1 s: t=0..1000 inclusive.
  EXPECT_EQ(app.statuses_sent(), 11u);

  // The observer decodes every status with the generated bindings: idle,
  // no faults, zero torque.
  lhal::CanFrame frame;
  uint32_t received = 0;
  while (dash.Receive(&frame)) {
    ASSERT_TRUE(VcuStatus::Matches(frame.id));
    ASSERT_EQ(frame.len, VcuStatus::kDlc);
    VcuStatus status = VcuStatus::FromFrame(frame);
    EXPECT_EQ(status.state, VcuState::kIdle);
    EXPECT_FALSE(status.faults_overtemp);
    EXPECT_EQ(status.torque_request_raw, 0);
    ++received;
  }
  EXPECT_EQ(received, 11u);
}

TEST(VcuApp, TracksPackStatusAndLatchesOvertemp) {
  lhal::host::TestClock clock;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can(&network);
  lhal::host::Can hvc(&network);   // fake HVC feeding pack status
  lhal::host::Can dash(&network);  // observer

  vcu::Peripherals p;
  p.clock = &clock;
  p.can = &vcu_can;
  vcu::App app(p);

  EXPECT_FALSE(app.pack_status_seen());

  // Healthy pack: 543.2 V, 40 degC coolant.
  HvcPackStatus pack;
  pack.set_pack_voltage(543.2f);
  pack.coolant_temp = 40;
  hvc.Send(pack.ToFrame());
  app.Step();

  ASSERT_TRUE(app.pack_status_seen());
  EXPECT_NEAR(app.pack_status().pack_voltage(), 543.2f, 0.1f);
  EXPECT_EQ(app.state(), VcuState::kIdle);

  // Coolant hits the limit: fault latches and the broadcast reflects it.
  pack.coolant_temp = vcu::App::kCoolantOvertempDegC;
  hvc.Send(pack.ToFrame());
  clock.Advance(vcu::App::kStatusPeriodMs);
  app.Step();

  EXPECT_EQ(app.state(), VcuState::kFault);

  // Cooling back down does not clear the latch.
  pack.coolant_temp = 30;
  hvc.Send(pack.ToFrame());
  clock.Advance(vcu::App::kStatusPeriodMs);
  app.Step();
  EXPECT_EQ(app.state(), VcuState::kFault);

  // The last status frame on the bus carries the fault.
  lhal::CanFrame frame;
  VcuStatus last;
  bool saw_status = false;
  while (dash.Receive(&frame)) {
    if (VcuStatus::Matches(frame.id)) {
      last = VcuStatus::FromFrame(frame);
      saw_status = true;
    }
  }
  ASSERT_TRUE(saw_status);
  EXPECT_EQ(last.state, VcuState::kFault);
  EXPECT_TRUE(last.faults_overtemp);
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
  EXPECT_EQ(app.statuses_sent(), 0u);
}

TEST(VcuApp, ShellReportsStateOverDebugUart) {
  lhal::host::TestClock clock;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can(&network);
  lhal::host::Can hvc(&network);
  lhal::host::Uart uart;

  vcu::Peripherals p;
  p.clock = &clock;
  p.can = &vcu_can;
  p.debug_uart = &uart;
  vcu::App app(p);
  uart.TakeTx();  // discard the boot banner (contents vary with git state)

  // Latch an overtemp fault, then query /state like tools/monitor would.
  HvcPackStatus pack;
  pack.coolant_temp = vcu::App::kCoolantOvertempDegC;
  hvc.Send(pack.ToFrame());
  app.Step();

  const std::string cmd = "/state\r";
  uart.InjectRx(reinterpret_cast<const uint8_t*>(cmd.data()), cmd.size());
  app.Step();

  const auto tx = uart.TakeTx();
  const std::string out(tx.begin(), tx.end());
  EXPECT_NE(out.find("state=fault"), std::string::npos) << out;
  EXPECT_NE(out.find("overtemp_latched=1"), std::string::npos) << out;
  EXPECT_NE(out.find("coolant 60 degC"), std::string::npos) << out;
}

}  // namespace
