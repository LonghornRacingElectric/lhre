#include "can/diag_frames.hpp"

#include <gtest/gtest.h>

#include <iterator>
#include <utility>

#include "can/field_ranges.hpp"
#include "lhal/host/can.hpp"
#include "lhre_can_vcu.hpp"

namespace {

namespace msg = lhre::can::vcu;

// Member pointers to one bool each, for the one-bit-at-a-time digital tests.
using Input = bool vcu::DigitalInputs::*;
using Output = bool vcu::DigitalOutputs::*;
using Field = bool msg::VcuDigitalIo::*;

// Decodes the one frame of type M that `bus` sent; fails if there isn't
// exactly one.
template <class M>
M Only(const lhal::host::Can& bus) {
  int n = 0;
  M out;
  for (const lhal::CanFrame& f : bus.sent()) {
    if (M::Matches(f.id)) {
      EXPECT_EQ(f.len, M::kDlc);
      out = M::FromFrame(f);
      ++n;
    }
  }
  EXPECT_EQ(n, 1);
  return out;
}

vcu::AnalogReadings Readings() {
  vcu::AnalogReadings a;
  a.apps1 = {1001, 1.25f, 1.9f};
  a.apps2 = {1002, 2.5f, 3.8f};
  a.bse1 = {1003, 0.75f, 1.1f};
  a.bse2 = {1004, 3.0f, 4.5f};
  a.steering = {1005, 1.65f, 2.5f};
  a.shutdown_in = {1006, 2.9f, 24.0f};
  a.shutdown_out = {1007, 2.8f, 23.5f};
  a.vrefint_counts = 1500;
  a.vdda = 3.3f;
  return a;
}

TEST(DiagFrames, NullBusSendsNothing) {
  EXPECT_EQ(vcu::SendAnalogDiag(nullptr, Readings(), {}), 0);
  EXPECT_EQ(
      vcu::SendDigitalDiag(nullptr, {}, {},
                           lhre::can::ShutdownLoopState::kClosed, false, false),
      0);
  EXPECT_EQ(vcu::SendHealthDiag(nullptr, lhal::ResetCause::kPin, 0, {}, {}), 0);
}

TEST(DiagFrames, AnalogSendsSixFramesWithEveryChannel) {
  lhal::host::Can bus;
  vcu::CalibratedValues cal;
  cal.apps1_travel = 0.25f;
  cal.apps2_travel = 0.5f;
  cal.bse1_psi = 120.5f;
  cal.bse2_psi = -3.0f;
  cal.steering_angle_deg = -45.0f;
  cal.steering_travel = 0.75f;

  EXPECT_EQ(vcu::SendAnalogDiag(&bus, Readings(), cal), 6);
  EXPECT_EQ(bus.sent().size(), 6u);

  const auto ca = Only<msg::VcuAdcCountsA>(bus);
  EXPECT_EQ(ca.apps1, 1001);
  EXPECT_EQ(ca.apps2, 1002);
  EXPECT_EQ(ca.bse1, 1003);
  EXPECT_EQ(ca.bse2, 1004);
  const auto cb = Only<msg::VcuAdcCountsB>(bus);
  EXPECT_EQ(cb.steering, 1005);
  EXPECT_EQ(cb.shutdown_in, 1006);
  EXPECT_EQ(cb.shutdown_out, 1007);
  EXPECT_EQ(cb.vrefint, 1500);

  // Pin volts for the sensors, source (loop-side) volts for the shutdown
  // taps: what each field is for, not just which number ends up where.
  const auto va = Only<msg::VcuAdcVoltsA>(bus);
  EXPECT_NEAR(va.apps1(), 1.25f, 1e-4f);
  EXPECT_NEAR(va.apps2(), 2.5f, 1e-4f);
  EXPECT_NEAR(va.bse1(), 0.75f, 1e-4f);
  EXPECT_NEAR(va.bse2(), 3.0f, 1e-4f);
  const auto vb = Only<msg::VcuAdcVoltsB>(bus);
  EXPECT_NEAR(vb.steering(), 1.65f, 1e-4f);
  EXPECT_NEAR(vb.shutdown_in_loop(), 24.0f, 0.01f);
  EXPECT_NEAR(vb.shutdown_out_loop(), 23.5f, 0.01f);
  EXPECT_NEAR(vb.vdda(), 3.3f, 1e-4f);

  const auto ka = Only<msg::VcuCalibratedA>(bus);
  EXPECT_NEAR(ka.apps1_travel(), 0.25f, 1e-4f);
  EXPECT_NEAR(ka.apps2_travel(), 0.5f, 1e-4f);
  EXPECT_NEAR(ka.bse1_pressure(), 120.5f, 0.1f);
  EXPECT_NEAR(ka.bse2_pressure(), -3.0f, 0.1f);
  const auto kb = Only<msg::VcuCalibratedB>(bus);
  EXPECT_NEAR(kb.steering_angle(), -45.0f, 0.004f);
  EXPECT_NEAR(kb.steering_travel(), 0.75f, 1e-4f);
}

// The generated setters don't clamp, so an out-of-range value would wrap
// (a shorted 7 V pin reading as 0.45 V). Every field pins at its limit.
TEST(DiagFrames, AnalogClampsEveryFieldAtItsRange) {
  vcu::AnalogReadings hi = Readings();
  for (vcu::AnalogReadings::Channel* c :
       {&hi.apps1, &hi.apps2, &hi.bse1, &hi.bse2, &hi.steering, &hi.shutdown_in,
        &hi.shutdown_out}) {
    c->pin_v = 7.0f;
    c->source_v = 1000.0f;
  }
  hi.vdda = 7.0f;
  vcu::CalibratedValues big;
  big.apps1_travel = big.apps2_travel = big.steering_travel = 5.0f;
  big.bse1_psi = big.bse2_psi = 5000.0f;
  big.steering_angle_deg = 200.0f;

  lhal::host::Can bus;
  vcu::SendAnalogDiag(&bus, hi, big);
  const auto va = Only<msg::VcuAdcVoltsA>(bus);
  EXPECT_EQ(va.apps1_raw, 0xFFFF);
  EXPECT_EQ(va.apps2_raw, 0xFFFF);
  EXPECT_EQ(va.bse1_raw, 0xFFFF);
  EXPECT_EQ(va.bse2_raw, 0xFFFF);
  const auto vb = Only<msg::VcuAdcVoltsB>(bus);
  EXPECT_EQ(vb.steering_raw, 0xFFFF);
  EXPECT_EQ(vb.shutdown_in_loop_raw, 0xFFFF);
  EXPECT_EQ(vb.shutdown_out_loop_raw, 0xFFFF);
  EXPECT_EQ(vb.vdda_raw, 0xFFFF);
  const auto ka = Only<msg::VcuCalibratedA>(bus);
  EXPECT_EQ(ka.apps1_travel_raw, 32767);
  EXPECT_EQ(ka.apps2_travel_raw, 32767);
  EXPECT_EQ(ka.bse1_pressure_raw, 32767);
  EXPECT_EQ(ka.bse2_pressure_raw, 32767);
  const auto kb = Only<msg::VcuCalibratedB>(bus);
  EXPECT_EQ(kb.steering_angle_raw, 32767);
  EXPECT_EQ(kb.steering_travel_raw, 32767);

  vcu::AnalogReadings lo = Readings();
  for (vcu::AnalogReadings::Channel* c :
       {&lo.apps1, &lo.apps2, &lo.bse1, &lo.bse2, &lo.steering, &lo.shutdown_in,
        &lo.shutdown_out}) {
    c->pin_v = -1.0f;
    c->source_v = -1.0f;
  }
  lo.vdda = -1.0f;
  vcu::CalibratedValues neg;
  neg.apps1_travel = neg.apps2_travel = neg.steering_travel = -5.0f;
  neg.bse1_psi = neg.bse2_psi = -5000.0f;
  neg.steering_angle_deg = -200.0f;

  bus.ClearSent();
  vcu::SendAnalogDiag(&bus, lo, neg);
  const auto va0 = Only<msg::VcuAdcVoltsA>(bus);
  EXPECT_EQ(va0.apps1_raw, 0);
  EXPECT_EQ(va0.bse2_raw, 0);
  const auto vb0 = Only<msg::VcuAdcVoltsB>(bus);
  EXPECT_EQ(vb0.steering_raw, 0);
  EXPECT_EQ(vb0.shutdown_in_loop_raw, 0);
  EXPECT_EQ(vb0.shutdown_out_loop_raw, 0);
  EXPECT_EQ(vb0.vdda_raw, 0);
  const auto ka0 = Only<msg::VcuCalibratedA>(bus);
  EXPECT_EQ(ka0.apps1_travel_raw, -32767);
  EXPECT_EQ(ka0.bse1_pressure_raw, -32767);
  const auto kb0 = Only<msg::VcuCalibratedB>(bus);
  EXPECT_EQ(kb0.steering_angle_raw, -32767);
  EXPECT_EQ(kb0.steering_travel_raw, -32767);
}

// One input at a time, so a swapped pair of fields can't pass.
TEST(DiagFrames, DigitalMapsEachInputToItsOwnBit) {
  const Input inputs[] = {
      &vcu::DigitalInputs::b1_disconnect,
      &vcu::DigitalInputs::b1_overvoltage,
      &vcu::DigitalInputs::b2_disconnect,
      &vcu::DigitalInputs::b2_overvoltage,
      &vcu::DigitalInputs::hard_braking,
      &vcu::DigitalInputs::combined_condition,
      &vcu::DigitalInputs::fault_confirmed,
      &vcu::DigitalInputs::latch_state,
      &vcu::DigitalInputs::linelock_out,
      &vcu::DigitalInputs::motor_power,
      &vcu::DigitalInputs::motor_disconnect,
      &vcu::DigitalInputs::rtd_pressed,
  };
  const Field fields[] = {
      &msg::VcuDigitalIo::inputs_b1_disconnect,
      &msg::VcuDigitalIo::inputs_b1_overvoltage,
      &msg::VcuDigitalIo::inputs_b2_disconnect,
      &msg::VcuDigitalIo::inputs_b2_overvoltage,
      &msg::VcuDigitalIo::inputs_hard_braking,
      &msg::VcuDigitalIo::inputs_combined_condition,
      &msg::VcuDigitalIo::inputs_fault_confirmed,
      &msg::VcuDigitalIo::inputs_latch_state,
      &msg::VcuDigitalIo::inputs_linelock_out,
      &msg::VcuDigitalIo::inputs_motor_power,
      &msg::VcuDigitalIo::inputs_motor_disconnect,
      &msg::VcuDigitalIo::inputs_rtd_switch,
  };
  static_assert(std::size(inputs) == std::size(fields));
  vcu::DigitalOutputs off;
  off.bms_led = off.imd_led = false;
  for (size_t i = 0; i < std::size(inputs); ++i) {
    SCOPED_TRACE(i);
    vcu::DigitalInputs in{};
    in.*inputs[i] = true;
    lhal::host::Can bus;
    EXPECT_EQ(vcu::SendDigitalDiag(&bus, in, off,
                                   lhre::can::ShutdownLoopState::kUnknown,
                                   false, false),
              1);
    const auto io = Only<msg::VcuDigitalIo>(bus);
    for (size_t j = 0; j < std::size(fields); ++j) {
      EXPECT_EQ(io.*fields[j], i == j) << "field " << j;
    }
  }
}

TEST(DiagFrames, DigitalMapsEachOutputAndTheBrakeFlags) {
  const Output outputs[] = {
      &vcu::DigitalOutputs::linelock_ctrl, &vcu::DigitalOutputs::amp_enable,
      &vcu::DigitalOutputs::bms_led,       &vcu::DigitalOutputs::imd_led,
      &vcu::DigitalOutputs::crit_stb,      &vcu::DigitalOutputs::data_stb,
  };
  const Field fields[] = {
      &msg::VcuDigitalIo::outputs_linelock_ctrl,
      &msg::VcuDigitalIo::outputs_amp_enable,
      &msg::VcuDigitalIo::outputs_bms_led,
      &msg::VcuDigitalIo::outputs_imd_led,
      &msg::VcuDigitalIo::outputs_crit_stb,
      &msg::VcuDigitalIo::outputs_data_stb,
  };
  static_assert(std::size(outputs) == std::size(fields));
  for (size_t i = 0; i < std::size(outputs); ++i) {
    SCOPED_TRACE(i);
    vcu::DigitalOutputs out;
    for (auto o : outputs) out.*o = false;
    out.*outputs[i] = true;
    lhal::host::Can bus;
    vcu::SendDigitalDiag(&bus, {}, out, lhre::can::ShutdownLoopState::kUnknown,
                         false, false);
    const auto io = Only<msg::VcuDigitalIo>(bus);
    for (size_t j = 0; j < std::size(fields); ++j) {
      EXPECT_EQ(io.*fields[j], i == j) << "field " << j;
    }
    EXPECT_FALSE(io.firmware_hard_brake);
    EXPECT_FALSE(io.hard_brake_mismatch);
  }

  lhal::host::Can bus;
  vcu::SendDigitalDiag(
      &bus, {}, {}, lhre::can::ShutdownLoopState::kBspdRelayOpen, true, false);
  auto io = Only<msg::VcuDigitalIo>(bus);
  EXPECT_EQ(io.shutdown_loop, lhre::can::ShutdownLoopState::kBspdRelayOpen);
  EXPECT_TRUE(io.firmware_hard_brake);
  EXPECT_FALSE(io.hard_brake_mismatch);

  bus.ClearSent();
  vcu::SendDigitalDiag(&bus, {}, {}, lhre::can::ShutdownLoopState::kImplausible,
                       false, true);
  io = Only<msg::VcuDigitalIo>(bus);
  EXPECT_EQ(io.shutdown_loop, lhre::can::ShutdownLoopState::kImplausible);
  EXPECT_FALSE(io.firmware_hard_brake);
  EXPECT_TRUE(io.hard_brake_mismatch);
}

// lhal::ResetCause is cast straight onto the spec enum, so the two have to
// keep the same numbering. Checked for every value, not just one.
TEST(DiagFrames, HealthCarriesEveryResetCause) {
  const std::pair<lhal::ResetCause, lhre::can::ResetCause> causes[] = {
      {lhal::ResetCause::kUnknown, lhre::can::ResetCause::kUnknown},
      {lhal::ResetCause::kPowerOn, lhre::can::ResetCause::kPowerOn},
      {lhal::ResetCause::kPin, lhre::can::ResetCause::kPin},
      {lhal::ResetCause::kSoftware, lhre::can::ResetCause::kSoftware},
      {lhal::ResetCause::kIndependentWatchdog,
       lhre::can::ResetCause::kIndependentWatchdog},
      {lhal::ResetCause::kWindowWatchdog,
       lhre::can::ResetCause::kWindowWatchdog},
      {lhal::ResetCause::kLowPower, lhre::can::ResetCause::kLowPower},
      {lhal::ResetCause::kOptionByteLoad,
       lhre::can::ResetCause::kOptionByteLoad},
  };
  for (const auto& [lhal_cause, spec_cause] : causes) {
    lhal::host::Can bus;
    EXPECT_EQ(vcu::SendHealthDiag(&bus, lhal_cause, 0, {}, {}), 1);
    EXPECT_EQ(Only<msg::VcuDiag>(bus).reset_cause, spec_cause);
  }
}

TEST(DiagFrames, HealthUptimeIsWholeSecondsAndSaturates) {
  const std::pair<uint32_t, uint16_t> cases[] = {
      {0, 0},
      {999, 0},
      {1000, 1},
      {65'535'999, 65535},
      {65'536'000, 65535},
      {0xFFFF'FFFF, 65535},
  };
  for (const auto& [ms, s] : cases) {
    lhal::host::Can bus;
    vcu::SendHealthDiag(&bus, lhal::ResetCause::kPowerOn, ms, {}, {});
    EXPECT_EQ(Only<msg::VcuDiag>(bus).uptime, s) << ms << " ms";
  }
}

TEST(DiagFrames, HealthCountersComeFromTheRightBusAndSaturate) {
  lhal::CanStats critical;
  critical.bus_off_count = 3;
  critical.tx_dropped = 4;
  lhal::CanStats data;
  data.bus_off_count = 5;
  data.tx_dropped = 6;
  lhal::host::Can bus;
  vcu::SendHealthDiag(&bus, lhal::ResetCause::kPowerOn, 0, critical, data);
  auto d = Only<msg::VcuDiag>(bus);
  EXPECT_EQ(d.critical_bus_off_count, 3);
  EXPECT_EQ(d.critical_tx_dropped, 4);
  EXPECT_EQ(d.data_bus_off_count, 5);
  EXPECT_EQ(d.data_tx_dropped, 6);

  critical.bus_off_count = 255;
  critical.tx_dropped = 256;
  data.bus_off_count = 100'000;
  data.tx_dropped = 0xFFFF'FFFF;
  bus.ClearSent();
  vcu::SendHealthDiag(&bus, lhal::ResetCause::kPowerOn, 0, critical, data);
  d = Only<msg::VcuDiag>(bus);
  EXPECT_EQ(d.critical_bus_off_count, 255);
  EXPECT_EQ(d.critical_tx_dropped, 255);
  EXPECT_EQ(d.data_bus_off_count, 255);
  EXPECT_EQ(d.data_tx_dropped, 255);
}

TEST(DiagFrames, Saturate8) {
  static_assert(vcu::Saturate8(0) == 0);
  static_assert(vcu::Saturate8(254) == 254);
  static_assert(vcu::Saturate8(255) == 255);
  static_assert(vcu::Saturate8(256) == 255);
  static_assert(vcu::Saturate8(0xFFFF'FFFF) == 255);
}

}  // namespace
