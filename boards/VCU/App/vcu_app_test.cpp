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

using lhre::can::hvc::ContactorStatus;
using VcuStateMessage = lhre::can::vcu::VcuState;

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

  // Simulate one second in 1 ms main-loop ticks.
  for (int i = 0; i < 1000; ++i) {
    app.Step();
    clock.Advance(1);
  }
  app.Step();

  // 100 ms blink period over 1 s: toggled at t=0, 100, ..., 1000 → 11
  // toggles → on.
  EXPECT_TRUE(led.Read());

  // 3 ms status period over 1 s: t=0, 3, ..., 999.
  EXPECT_EQ(app.statuses_sent(), 334u);

  // The observer decodes every real 2026 VCU_STATE packet.
  lhal::CanFrame frame;
  uint32_t received = 0;
  while (dash.Receive(&frame)) {
    ASSERT_TRUE(VcuStateMessage::Matches(frame.id));
    ASSERT_EQ(frame.len, VcuStateMessage::kDlc);
    VcuStateMessage status = VcuStateMessage::FromFrame(frame);
    EXPECT_EQ(status.prndl_state, static_cast<uint8_t>(vcu::State::kIdle));
    EXPECT_FALSE(status.stomp_fault);
    EXPECT_FALSE(status.ready_to_drive_buzzer);
    ++received;
  }
  EXPECT_EQ(received, 334u);
}

TEST(VcuApp, TracksHvcContactorsAndEntersDrive) {
  lhal::host::TestClock clock;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can(&network);
  lhal::host::Can hvc(&network);   // fake HVC feeding contactor status
  lhal::host::Can dash(&network);  // observer

  vcu::Peripherals p;
  p.clock = &clock;
  p.can = &vcu_can;
  vcu::App app(p);

  EXPECT_FALSE(app.contactor_status_seen());

  ContactorStatus contactors;
  contactors.positive_hv_contactor = true;
  hvc.Send(contactors.ToFrame());
  app.Step();

  ASSERT_TRUE(app.contactor_status_seen());
  EXPECT_TRUE(app.contactor_status().positive_hv_contactor);
  EXPECT_EQ(app.state(), vcu::State::kIdle);

  contactors.negative_hv_contactor = true;
  hvc.Send(contactors.ToFrame());
  clock.Advance(vcu::App::kStatusPeriodMs);
  app.Step();

  EXPECT_EQ(app.state(), vcu::State::kDrive);

  // The last status frame reflects the ready-to-drive state.
  lhal::CanFrame frame;
  VcuStateMessage last;
  bool saw_status = false;
  while (dash.Receive(&frame)) {
    if (VcuStateMessage::Matches(frame.id)) {
      last = VcuStateMessage::FromFrame(frame);
      saw_status = true;
    }
  }
  ASSERT_TRUE(saw_status);
  EXPECT_EQ(last.prndl_state, static_cast<uint8_t>(vcu::State::kDrive));
  EXPECT_TRUE(last.ready_to_drive_buzzer);
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

  ContactorStatus contactors;
  contactors.positive_hv_contactor = true;
  contactors.negative_hv_contactor = true;
  hvc.Send(contactors.ToFrame());
  app.Step();

  const std::string cmd = "/state\r";
  uart.InjectRx(reinterpret_cast<const uint8_t*>(cmd.data()), cmd.size());
  app.Step();

  const auto tx = uart.TakeTx();
  const std::string out(tx.begin(), tx.end());
  EXPECT_NE(out.find("state=drive"), std::string::npos) << out;
  EXPECT_NE(out.find("positive=1"), std::string::npos) << out;
  EXPECT_NE(out.find("negative=1"), std::string::npos) << out;
}

TEST(VcuApp, LoggerOutputsTimestampedLogsOverDebugUart) {
  lhal::host::TestClock clock;
  lhal::host::Uart uart;

  vcu::Peripherals p;
  p.clock = &clock;
  p.debug_uart = &uart;
  vcu::App app(p);
  uart.TakeTx();  // discard the boot banner

  clock.Advance(123);
  app.logger().Info("test info log");
  app.Step();

  const auto tx = uart.TakeTx();
  const std::string out(tx.begin(), tx.end());
  EXPECT_NE(out.find("[123]"), std::string::npos) << out;
  EXPECT_NE(out.find("[INFO]"), std::string::npos) << out;
  EXPECT_NE(out.find("test info log"), std::string::npos) << out;
}

}  // namespace
