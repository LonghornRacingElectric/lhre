#include <string>

#include <gtest/gtest.h>

#include "app/serial_report.hpp"
#include "lhal/host/uart.hpp"

namespace {
TEST(SerialReport, IncludesReadableSectionsUnitsAndFaultReasons) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  vcu::ControlSnapshot s;
  s.now_ms = 990;
  s.state = lhre::can::VcuState::kIdle;
  s.adc_fresh = true;
  s.analog.apps1 = {1234, 1.234f, 2.468f};
  s.faults = vcu::kFaultAdcStale | vcu::kFaultSteeringRange;
  s.primary_fault = vcu::PrimaryFault(s.faults);
  s.torque_command = -12.5f;
  vcu::SerialHealth h;
  h.uptime_ms = 1000;
  h.critical.tx_dropped = 42;
  vcu::PrintSerialReport(console, s, h, lhal::ResetCause::kUnknown);
  const auto bytes = uart.TakeTx();
  const std::string text(bytes.begin(), bytes.end());
  for (const char* label :
       {"VEHICLE CONTROL UNIT", "state=IDLE", "Sample age: 10 ms",
        "ANALOG INPUTS", "Counts", "Pin mV", "Source mV", "PEDALS / STEERING",
        "DIGITAL I/O", "TORQUE / MOTOR / BATTERY", "CAN HEALTH",
        "TX dropped=42", "ADC_STALE", "STEERING_RANGE", "[TORQUE CUT]",
        "[diagnostic]", "-12500 mNm"}) {
    EXPECT_NE(text.find(label), std::string::npos) << label;
  }
  EXPECT_EQ(text.find('\x1b'), std::string::npos);
}

TEST(SerialReport, CompactShowsStateAdcAndGpioWithoutDetailedSections) {
  lhal::host::Uart uart;
  longhorn::Console console(&uart);
  vcu::ControlSnapshot s;
  s.state = lhre::can::VcuState::kIdle;
  s.analog.apps1 = {1000, 1.0f, 1.5f};
  s.inputs.rtd_pressed = true;
  vcu::PrintCompactSerialReport(console, s);
  const auto bytes = uart.TakeTx();
  const std::string text(bytes.begin(), bytes.end());
  for (const char* label : {"state=IDLE", "APPS1=1000/1000", "APPS2=", "BSE1=",
                            "BSE2=", "STEER=", "SD_IN=", "SD_OUT=", "VREFINT=",
                            "VDDA=", "GPIO IN", "RTD=1", "GPIO OUT"}) {
    EXPECT_NE(text.find(label), std::string::npos) << label;
  }
  EXPECT_EQ(text.find("CAN HEALTH"), std::string::npos);
  EXPECT_EQ(text.find("TORQUE / MOTOR"), std::string::npos);
}

TEST(SerialReport, DisconnectedMonitorProducesNoWrites) {
  lhal::host::Uart uart;
  uart.set_connected(false);
  longhorn::Console console(&uart);
  vcu::PrintSerialReport(console, {}, {}, lhal::ResetCause::kUnknown);
  EXPECT_TRUE(uart.TakeTx().empty());
}
}  // namespace
