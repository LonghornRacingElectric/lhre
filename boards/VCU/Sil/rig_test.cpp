#include "Sil/rig.hpp"

#include <gtest/gtest.h>

#include <cmath>
#include <cstdint>
#include <map>
#include <memory>
#include <vector>

#include "Sil/sensors.hpp"
#include "app/vcu_app.hpp"
#include "control/steering.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_inverter.hpp"
#include "lhre_can_pdu.hpp"
#include "lhre_can_usm.hpp"
#include "lhre_can_vcu.hpp"
#include "safety/faults.hpp"
#include "test/bench.hpp"

namespace vcu::sil {
namespace {

using lhre::can::HvcState;
using VcuState = lhre::can::VcuStateMachine;

constexpr float kPi = 3.14159265358979f;

// The car powered up and parked: HVC energized, loop closed, brake held.
RigInputs ParkedCar() {
  RigInputs in;
  in.hvc_state = HvcState::kEnergized;
  in.contactors_closed = true;
  in.shutdown_loop_v = 24.0f;
  in.brake_front_psi = 300.0f;
  in.brake_rear_psi = 300.0f;
  in.dc_bus_voltage_v = 574.0f;
  in.min_cell_v = 4.1f;
  in.max_cell_v = 4.1f;
  return in;
}

// Advances in 3 ms steps, as the co-simulation host does.
RigOutputs Hold(Rig& rig, const RigInputs& in, uint32_t ms) {
  RigOutputs out;
  for (uint32_t t = 0; t < ms; t += 3) {
    out = rig.Advance(in, 3);
  }
  return out;
}

// Parks, presses RTD with the brake held, then releases the brake.
RigInputs ReachDrive(Rig& rig) {
  RigInputs in = ParkedCar();
  Hold(rig, in, 300);
  in.rtd_pressed = true;
  Hold(rig, in, 60);
  in.brake_front_psi = 0.0f;
  in.brake_rear_psi = 0.0f;
  const RigOutputs out = Hold(rig, in, 60);
  EXPECT_EQ(out.snapshot.state, VcuState::kDrive);
  EXPECT_EQ(out.snapshot.faults & kTorqueCutFaults, 0u);
  return in;
}

TEST(SilSensors, FitFieldKeepsTheRawTypeAndFlagsClamps) {
  bool c = true;
  EXPECT_FLOAT_EQ(FitField(327.67f, -32768, 32767, 0.01f, &c), 327.67f);
  EXPECT_FALSE(c);
  EXPECT_FLOAT_EQ(FitField(400.0f, -32768, 32767, 0.01f, &c), 327.67f);
  EXPECT_TRUE(c);
  EXPECT_FLOAT_EQ(FitField(-1.0f, 0, 65535, 0.05f, &c), 0.0f);
  EXPECT_TRUE(c);
  EXPECT_FLOAT_EQ(FitField(NAN, 0, 65535, 0.05f, &c), 0.0f);
  EXPECT_TRUE(c);
  // Within half a count of the end the setter rounds to the end; past it
  // the value is clamped.
  lhre::can::usm::AccelerationVectorUnsprungWheelSpeedFl w;
  w.set_wheel_speed(FitField(327.674f, -32768, 32767, 0.01f, &c));
  EXPECT_FALSE(c);
  EXPECT_EQ(w.wheel_speed_raw, 32767);
  w.set_wheel_speed(FitField(327.676f, -32768, 32767, 0.01f, &c));
  EXPECT_TRUE(c);
  EXPECT_EQ(w.wheel_speed_raw, 32767);
  w.set_wheel_speed(FitField(-327.684f, -32768, 32767, 0.01f, &c));
  EXPECT_FALSE(c);
  EXPECT_EQ(w.wheel_speed_raw, -32768);
  // The setter's round-to-nearest stays inside int16 at both ends.
  w.set_wheel_speed(FitField(1e9f, -32768, 32767, 0.01f, &c));
  EXPECT_EQ(w.wheel_speed_raw, 32767);
  w.set_wheel_speed(FitField(-1e9f, -32768, 32767, 0.01f, &c));
  EXPECT_EQ(w.wheel_speed_raw, -32768);
  lhre::can::pdu::Bse3 b;
  b.set_brake_pressure_3(FitField(-0.02f, 0, 65535, 0.05f, &c));
  EXPECT_FALSE(c);
  EXPECT_EQ(b.brake_pressure_3_raw, 0);
  b.set_brake_pressure_3(FitField(1e9f, 0, 65535, 0.05f, &c));
  EXPECT_TRUE(c);
  EXPECT_EQ(b.brake_pressure_3_raw, 65535);
  // The largest float under half a count below zero: the setter's own
  // rounding would reach -1 if FitField passed it through (UBSan traps).
  for (const float scale : {0.1f, 0.0001f, 0.05f}) {
    const float v = -0.49999997f * scale;
    EXPECT_EQ(FitField(v, 0, 65535, scale, &c), 0.0f) << scale;
    EXPECT_FALSE(c) << scale;
  }
  lhre::can::hvc::BatteryCellLimits cells;
  cells.set_min_cell_voltage(
      FitField(-0.49999997f * 0.0001f, 0, 65535, 0.0001f, &c));
  EXPECT_EQ(cells.min_cell_voltage_raw, 0);
}

TEST(SilSensors, MotorRpmRoundsAndSaturates) {
  bool c = true;
  EXPECT_EQ(MotorRpm(0.0f, &c), 0);
  EXPECT_FALSE(c);
  EXPECT_EQ(MotorRpm(100.0f * kPi / 30.0f, &c), 100);
  EXPECT_EQ(MotorRpm(-50.0f, &c), -477);  // -477.46
  EXPECT_FALSE(c);
  EXPECT_EQ(MotorRpm(4000.0f, &c), 32767);
  EXPECT_TRUE(c);
  EXPECT_EQ(MotorRpm(-INFINITY, &c), 0);
  EXPECT_TRUE(c);
  // Finite but past float range once scaled: saturates, never reads as 0.
  EXPECT_EQ(MotorRpm(1e38f, &c), 32767);
  EXPECT_TRUE(c);
  EXPECT_EQ(MotorRpm(-1e38f, &c), -32767);
}

TEST(SilSensors, WheelSpeedIsAMagnitude) {
  bool c = true;
  EXPECT_FLOAT_EQ(WheelSpeed(-12.5f, &c), 12.5f);
  EXPECT_FALSE(c);
}

TEST(SilSensors, SteeringVoltsInvertsComputeSteering) {
  const SteeringParams p;
  for (float deg = -188.0f; deg <= 180.0f; deg += 0.25f) {
    bool clamped = true;
    const float v = SteeringVolts(deg, p, &clamped);
    EXPECT_FALSE(clamped) << deg;
    EXPECT_GE(v, p.v_at_0) << deg;
    EXPECT_LE(v, p.v_at_100) << deg;
    EXPECT_NEAR(ComputeSteering(v, p).angle_deg, deg, 1e-3f) << deg;
  }
  bool clamped = false;
  const float high =
      ComputeSteering(SteeringVolts(400.0f, p, &clamped), p).angle_deg;
  EXPECT_TRUE(clamped);
  EXPECT_GT(high, 179.0f);
  const float low =
      ComputeSteering(SteeringVolts(-400.0f, p, &clamped), p).angle_deg;
  EXPECT_TRUE(clamped);
  EXPECT_LT(low, -187.0f);
}

TEST(SilRig, StartupWalksEveryStateIntoDrive) {
  Rig rig;
  RigInputs in;
  in.shutdown_loop_v = 24.0f;
  in.brake_front_psi = 300.0f;
  in.brake_rear_psi = 300.0f;
  in.dc_bus_voltage_v = 574.0f;
  std::vector<VcuState> seen;
  uint32_t drive_ms = 0;
  for (uint32_t t = 0; t < 1500; t += 3) {
    in.hvc_state = t < 200   ? HvcState::kNotEnergized
                   : t < 600 ? HvcState::kPrecharging
                             : HvcState::kEnergized;
    in.contactors_closed = t >= 600;
    in.rtd_pressed = t >= 1000;
    const RigOutputs out = rig.Advance(in, 3);
    if (seen.empty() || seen.back() != out.snapshot.state) {
      seen.push_back(out.snapshot.state);
    }
    if (out.snapshot.state == VcuState::kDrive && drive_ms == 0) {
      drive_ms = out.snapshot.now_ms;
      EXPECT_EQ(out.snapshot.faults & kTorqueCutFaults, 0u);
    }
  }
  const std::vector<VcuState> expected = {
      VcuState::kIdle,    VcuState::kPrecharge, VcuState::kTsActive,
      VcuState::kRtdWait, VcuState::kDrive,
  };
  EXPECT_EQ(seen, expected);
  // The RTD debounce is 20 ms.
  EXPECT_GE(drive_ms, 1020u);
  EXPECT_LE(drive_ms, 1030u);
}

TEST(SilRig, OneTorqueFramePerControlTick) {
  Rig rig;
  const RigInputs in = ParkedCar();
  for (int i = 0; i < 1000; ++i) {
    ASSERT_EQ(rig.Advance(in, 3).torque_frames, 1u) << i;
  }
  EXPECT_EQ(rig.Advance(in, 30).torque_frames, 10u);
}

TEST(SilRig, TorqueCommandIsWhatTheInverterDecodes) {
  Rig rig;
  RigInputs in = ParkedCar();
  RigOutputs out = Hold(rig, in, 300);
  EXPECT_EQ(out.snapshot.state, VcuState::kRtdWait);
  EXPECT_FALSE(out.command.enable);
  EXPECT_EQ(out.command.torque_request_nm, 0.0f);
  EXPECT_FLOAT_EQ(out.command.torque_limit_nm, 230.0f);

  in = ReachDrive(rig);
  in.accelerator_travel = 1.0f;
  in.motor_speed_rad_s = 300.0f;
  for (int i = 0; i < 100; ++i) {
    out = rig.Advance(in, 3);
    ASSERT_EQ(out.torque_frames, 1u);
    EXPECT_TRUE(out.command.enable);
    EXPECT_TRUE(out.command.direction);
    EXPECT_NEAR(out.command.torque_request_nm,
                std::round(rig.app().torque_command() * 10.0f) / 10.0f, 1e-4f);
  }
  // 2865 rpm on the stock map: well above zero and under the 230 N.m clamp.
  EXPECT_GT(out.command.torque_request_nm, 150.0f);
  EXPECT_LE(out.command.torque_request_nm, 230.0f);
}

TEST(SilRig, DriverInputsReadBackThroughTheFirmware) {
  Rig rig;
  RigInputs in = ParkedCar();
  for (const float travel : {0.0f, 0.1f, 0.25f, 0.5f, 0.9f, 1.0f}) {
    in.accelerator_travel = travel;
    const RigOutputs out = Hold(rig, in, 30);
    EXPECT_NEAR(out.snapshot.pedals.apps1_travel, travel, 1e-3f);
    EXPECT_NEAR(out.snapshot.pedals.apps2_travel, travel, 1e-3f);
  }
  in.accelerator_travel = 0.0f;
  const float pairs[][2] = {{0, 0}, {100, 200}, {1500, 750}, {3000, 0}};
  for (const auto& pair : pairs) {
    in.brake_front_psi = pair[0];
    in.brake_rear_psi = pair[1];
    const RigOutputs out = Hold(rig, in, 30);
    // One ADC count is about 0.9 psi.
    EXPECT_NEAR(out.snapshot.pedals.bse1_psi, pair[0], 1.0f);
    EXPECT_NEAR(out.snapshot.pedals.bse2_psi, pair[1], 1.0f);
    EXPECT_NEAR(out.snapshot.bse3_psi, pair[1], 0.05f);
  }
  for (const float deg :
       {-180.0f, -120.0f, -45.0f, -1.0f, 0.0f, 1.0f, 45.0f, 120.0f, 180.0f}) {
    in.steering_angle_deg = deg;
    const RigOutputs out = Hold(rig, in, 30);
    // One ADC count is about 0.11 degrees.
    EXPECT_NEAR(out.snapshot.steering.angle_deg, deg, 0.15f) << deg;
    EXPECT_EQ(out.saturated & kSatSteering, 0u) << deg;
  }
}

TEST(SilRig, CarModelValuesReadBackThroughTheFirmware) {
  Rig rig;
  RigInputs in = ReachDrive(rig);
  for (const float w : {0.0f, 10.0f, 314.159f, -50.0f, 600.0f}) {
    in.motor_speed_rad_s = w;
    const RigOutputs out = Hold(rig, in, 30);
    EXPECT_TRUE(out.snapshot.motor_speed_valid);
    EXPECT_EQ(out.snapshot.motor_rpm, std::lround(w * 30.0f / kPi)) << w;
  }
  in.dc_bus_voltage_v = 560.0f;
  in.dc_bus_current_a = 100.0f;
  // The current frame is 10 Hz; wait for a fresh one.
  RigOutputs out = Hold(rig, in, 210);
  EXPECT_NEAR(out.snapshot.torque.power_w, 56000.0f, 10.0f);
  in.dc_bus_current_a = -40.0f;
  out = Hold(rig, in, 210);
  EXPECT_NEAR(out.snapshot.torque.power_w, -22400.0f, 10.0f);

  in.wheel_speed_rad_s[0] = 50.0f;
  in.wheel_speed_rad_s[1] = 51.0f;
  in.wheel_speed_rad_s[2] = -55.0f;  // read as a magnitude
  in.wheel_speed_rad_s[3] = 56.0f;
  out = Hold(rig, in, 30);
  EXPECT_TRUE(out.snapshot.wheel_control.valid);
  const float reference = 51.0f * 0.2045f;
  EXPECT_NEAR(out.snapshot.wheel_control.reference_m_s, reference, 1e-3f);
  EXPECT_NEAR(out.snapshot.wheel_control.drive_slip,
              (56.0f * 0.2045f - reference) / reference, 1e-3f);
  EXPECT_EQ(out.saturated, 0u);
}

TEST(SilRig, FramesGoOutAtTheirSpecRates) {
  Rig rig;
  lhal::host::Can critical_spy{&rig.bench().critical_net};
  lhal::host::Can data_spy{&rig.bench().data_net};
  const RigInputs in = ParkedCar();
  Hold(rig, in, 999);  // ms 0..998: a whole 1 s window from t = 0
  std::map<uint32_t, int> counts;
  lhal::CanFrame f;
  while (critical_spy.Receive(&f)) ++counts[f.id];
  while (data_spy.Receive(&f)) ++counts[f.id];
  namespace can = lhre::can;
  const std::map<uint32_t, int> expected = {
      {can::hvc::ContactorStatus::kFrameId, 100},
      {can::hvc::BatteryCellLimits::kFrameId, 10},
      {can::hvc::IndicatorsShutdownStatus::kFrameId, 10},
      {can::inverter::InverterFaults::kFrameId, 100},
      {can::inverter::InverterDetails::kFrameId, 100},
      {can::inverter::InverterSpeed::kFrameId, 333},
      {can::inverter::InverterVoltage::kFrameId, 100},
      {can::inverter::InverterCurrent::kFrameId, 10},
      {can::pdu::Bse3::kFrameId, 100},
      {can::dui::DuiDriverInputs::kFrameId, 10},
      {can::usm::AccelerationVectorUnsprungWheelSpeedFl::kFrameId, 333},
      {can::usm::AccelerationVectorUnsprungWheelSpeedFr::kFrameId, 333},
      {can::usm::AccelerationVectorUnsprungWheelSpeedRl::kFrameId, 333},
      {can::usm::AccelerationVectorUnsprungWheelSpeedRr::kFrameId, 333},
  };
  for (const auto& [id, n] : expected) {
    EXPECT_EQ(counts[id], n) << "frame 0x" << std::hex << id;
  }
  // The VCU's own torque command goes out once per 3 ms tick.
  EXPECT_EQ(counts[can::vcu::InverterTorqueCommand::kFrameId], 333);
}

TEST(SilRig, HostBuffersStayEmpty) {
  Rig rig;
  RigInputs in = ReachDrive(rig);
  in.accelerator_travel = 0.5f;
  in.motor_speed_rad_s = 200.0f;
  Hold(rig, in, 10000);
  bench::Bench& b = rig.bench();
  EXPECT_TRUE(b.vcu_critical.sent().empty());
  EXPECT_TRUE(b.vcu_data.sent().empty());
  EXPECT_TRUE(b.car.sent().empty());
  EXPECT_TRUE(b.logger.sent().empty());
  lhal::CanFrame f;
  EXPECT_FALSE(b.car.Receive(&f));
  EXPECT_FALSE(b.logger.Receive(&f));
  EXPECT_TRUE(b.vcp.tx_data().empty());
  EXPECT_TRUE(b.usb.tx_data().empty());
}

TEST(SilRig, ConsoleCarriesTheStateLog) {
  Rig rig;
  ReachDrive(rig);
  const std::string text = rig.TakeConsole();
  EXPECT_NE(text.find("-> DRIVE"), std::string::npos) << text;
  EXPECT_TRUE(rig.TakeConsole().empty());
  EXPECT_EQ(rig.console_dropped(), 0u);
}

// EV.4.7 seen through the rig: throttle while the filtered brake is still
// over the pressed threshold cuts torque until the pedal is released.
TEST(SilRig, BrakeOverThrottleHoldsTorqueAtZeroUntilPedalRelease) {
  Rig rig;
  RigInputs in = ParkedCar();
  Hold(rig, in, 300);
  in.rtd_pressed = true;
  Hold(rig, in, 60);
  in.brake_front_psi = in.brake_rear_psi = 0.0f;
  in.accelerator_travel = 1.0f;
  in.motor_speed_rad_s = 300.0f;
  RigOutputs out = Hold(rig, in, 300);
  EXPECT_EQ(out.snapshot.state, VcuState::kDrive);
  EXPECT_NE(out.snapshot.faults & kFaultBrakeOverThrottle, 0u);
  EXPECT_TRUE(out.command.enable);
  EXPECT_EQ(out.command.torque_request_nm, 0.0f);
  in.accelerator_travel = 0.0f;
  Hold(rig, in, 30);
  in.accelerator_travel = 1.0f;
  out = Hold(rig, in, 300);
  EXPECT_EQ(out.snapshot.faults & kFaultBrakeOverThrottle, 0u);
  EXPECT_GT(out.command.torque_request_nm, 150.0f);
}

TEST(SilRig, HardBrakeComparatorAgreesWithTheFirmware) {
  Rig rig;
  RigInputs in = ParkedCar();
  for (const float psi : {100.0f, 1500.0f, 3000.0f, 0.0f}) {
    in.brake_front_psi = psi;
    in.brake_rear_psi = psi;
    const RigOutputs out = Hold(rig, in, 300);
    EXPECT_EQ(out.snapshot.faults & kFaultHardBrakeMismatch, 0u) << psi;
    EXPECT_EQ(rig.bench().hard_braking.Read(), psi > 600.0f) << psi;
  }
  // Around the 0.80 V pin threshold (about 531 psi) ADC rounding decides
  // the firmware's view; the comparator must land on the same side.
  for (float psi = 520.0f; psi <= 545.0f; psi += 0.25f) {
    in.brake_front_psi = psi;
    in.brake_rear_psi = psi;
    const RigOutputs out = Hold(rig, in, 60);
    ASSERT_EQ(out.snapshot.faults & kFaultHardBrakeMismatch, 0u) << psi;
  }
}

TEST(SilRig, ClampedCarModelValuesAreFlagged) {
  Rig rig;
  RigInputs in = ParkedCar();
  EXPECT_EQ(rig.Advance(in, 3).saturated, 0u);
  in.wheel_speed_rad_s[2] = 400.0f;
  in.motor_speed_rad_s = 4000.0f;
  in.motor_torque_nm = 1e6f;
  in.dc_bus_voltage_v = 7000.0f;
  in.dc_bus_current_a = -5000.0f;
  in.max_cell_v = 7.0f;
  in.steering_angle_deg = 500.0f;
  const RigOutputs out = Hold(rig, in, 30);
  EXPECT_EQ(out.saturated, kSatWheelSpeed | kSatMotorSpeed | kSatMotorTorque |
                               kSatBusVoltage | kSatBusCurrent |
                               kSatCellVoltage | kSatSteering);
  EXPECT_EQ(out.snapshot.motor_rpm, 32767.0f);
  // A clamped steering angle reads back at the end of the range it asked
  // for, never wrapped to the other end.
  EXPECT_GT(out.snapshot.steering.angle_deg, 180.0f);
  in.steering_angle_deg = -500.0f;
  EXPECT_LT(Hold(rig, in, 30).snapshot.steering.angle_deg, -188.0f);
}

// A longitudinal car as in closed_loop_test, closed through the rig: the
// firmware's torque command drives it and it reports speed, wheel speeds
// and DC power back.
struct ToyCar {
  static constexpr float kMassKg = 243.742f;
  static constexpr float kWheelRadiusM = 0.2045f;
  static constexpr float kFinalDrive = 3.31f;
  static constexpr float kRotorJ = 0.02521f;
  static constexpr float kEfficiency = 0.9f;
  float v = 0.0f;

  void Step(float torque_nm, float dt_s) {
    const float m_eff = kMassKg + kRotorJ * kFinalDrive * kFinalDrive /
                                      (kWheelRadiusM * kWheelRadiusM);
    v += torque_nm * kFinalDrive / kWheelRadiusM / m_eff * dt_s;
  }
  void Report(RigInputs* in, float torque_nm) const {
    const float wheel = v / kWheelRadiusM;
    for (float& w : in->wheel_speed_rad_s) w = wheel;
    in->motor_speed_rad_s = wheel * kFinalDrive;
    in->motor_torque_nm = torque_nm;
    in->dc_bus_current_a =
        torque_nm * in->motor_speed_rad_s / kEfficiency / in->dc_bus_voltage_v;
  }
};

TEST(SilRig, DrivesAToyCarWithoutFaults) {
  Rig rig;
  RigInputs in = ReachDrive(rig);
  ToyCar car;
  in.accelerator_travel = 1.0f;
  float torque = 0.0f;
  for (uint32_t t = 0; t < 4000; t += 3) {
    car.Report(&in, torque);
    const RigOutputs out = rig.Advance(in, 3);
    ASSERT_EQ(out.snapshot.state, VcuState::kDrive) << t;
    ASSERT_EQ(out.snapshot.faults & kTorqueCutFaults, 0u) << t;
    ASSERT_EQ(out.saturated, 0u) << t;
    torque = out.command.enable ? out.command.torque_request_nm : 0.0f;
    car.Step(torque, 0.003f);
  }
  EXPECT_GT(car.v, 20.0f);
}

TEST(SilRig, TwoRigsInOneProcessAreIndependentAndRepeatable) {
  auto a = std::make_unique<Rig>();
  auto b = std::make_unique<Rig>();
  RigInputs in = ParkedCar();
  for (uint32_t t = 0; t < 3000; t += 3) {
    in.rtd_pressed = t >= 400;
    if (t >= 600) {
      in.brake_front_psi = in.brake_rear_psi = 0.0f;
      in.accelerator_travel = 0.6f;
      in.motor_speed_rad_s = 0.2f * static_cast<float>(t - 600);
      in.dc_bus_current_a = 0.05f * static_cast<float>(t - 600);
    }
    const RigOutputs x = a->Advance(in, 3);
    const RigOutputs y = b->Advance(in, 3);
    ASSERT_EQ(x.snapshot.state, y.snapshot.state) << t;
    ASSERT_EQ(x.snapshot.faults, y.snapshot.faults) << t;
    ASSERT_EQ(x.command.torque_request_nm, y.command.torque_request_nm) << t;
    ASSERT_EQ(x.snapshot.torque.power_trim_w, y.snapshot.torque.power_trim_w)
        << t;
  }
  // A fresh rig replays the same run from boot.
  a = std::make_unique<Rig>();
  in = ParkedCar();
  const RigOutputs first = a->Advance(in, 3);
  b = std::make_unique<Rig>();
  const RigOutputs second = b->Advance(in, 3);
  EXPECT_EQ(first.snapshot.state, second.snapshot.state);
  EXPECT_EQ(first.now_ms, second.now_ms);
}

TEST(SilRig, EventModeSelectsTheFirmwareParameterSet) {
  Rig rig(lhre::can::EventMode::kAcceleration);
  EXPECT_EQ(rig.bench().params.event_mode, lhre::can::EventMode::kAcceleration);
  RigInputs in = ReachDrive(rig);
  in.accelerator_travel = 1.0f;
  in.motor_speed_rad_s = 100.0f;  // 955 rpm, on the flat part of the map
  const RigOutputs out = Hold(rig, in, 300);
  EXPECT_EQ(out.snapshot.event_mode, lhre::can::EventMode::kAcceleration);
  EXPECT_NEAR(out.command.torque_request_nm,
              ParamsFor(lhre::can::EventMode::kAcceleration)
                  .torque.power_limit_torque[0],
              0.05f);
}

// The README's step contract: the control tick on the first pass sees the
// car values given to this advance, not the previous advance's.
TEST(SilRig, ControlTickSeesThisAdvancesCarValues) {
  Rig rig;
  RigInputs in = ReachDrive(rig);
  Hold(rig, in, 30);
  in.motor_speed_rad_s = 300.0f;
  for (float& w : in.wheel_speed_rad_s) w = 40.0f;
  const RigOutputs out = rig.Advance(in, 3);
  EXPECT_EQ(out.snapshot.motor_rpm, std::lround(300.0f * 30.0f / kPi));
  EXPECT_NEAR(out.snapshot.wheel_control.reference_m_s, 40.0f * 0.2045f, 1e-3f);
}

// One advance over several ticks reports the last tick's frame and snapshot.
TEST(SilRig, LongAdvanceReportsTheLastTick) {
  Rig rig;
  RigInputs in = ReachDrive(rig);
  in.motor_speed_rad_s = 300.0f;
  in.accelerator_travel = 1.0f;
  const RigOutputs out = rig.Advance(in, 30);
  EXPECT_EQ(out.torque_frames, 10u);
  EXPECT_EQ(out.snapshot.now_ms, out.now_ms - 3);
  EXPECT_NEAR(out.command.torque_request_nm,
              std::round(rig.app().torque_command() * 10.0f) / 10.0f, 1e-4f);
  EXPECT_NEAR(out.snapshot.torque_command, rig.app().torque_command(), 1e-6f);
}

// The comparator needs both circuits over the threshold, like the
// firmware's own cross-check; one circuit alone raises nothing.
TEST(SilRig, HardBrakeNeedsBothCircuits) {
  Rig rig;
  RigInputs in = ParkedCar();
  const float pairs[][2] = {{1500, 0}, {0, 1500}, {1500, 300}};
  for (const auto& pair : pairs) {
    in.brake_front_psi = pair[0];
    in.brake_rear_psi = pair[1];
    const RigOutputs out = Hold(rig, in, 300);
    EXPECT_FALSE(rig.bench().hard_braking.Read()) << pair[0] << "/" << pair[1];
    EXPECT_EQ(out.snapshot.faults & kFaultHardBrakeMismatch, 0u)
        << pair[0] << "/" << pair[1];
  }
}

// Opening the loop drops both sense taps: upstream open, not implausible.
TEST(SilRig, OpenLoopDropsBothTaps) {
  Rig rig;
  RigInputs in = ReachDrive(rig);
  in.shutdown_loop_v = 0.0f;
  const RigOutputs out = Hold(rig, in, 60);
  EXPECT_EQ(out.snapshot.loop, lhre::can::ShutdownLoopState::kUpstreamOpen);
  EXPECT_EQ(out.snapshot.faults & kFaultShutdownSenseImplausible, 0u);
  EXPECT_NE(out.snapshot.faults & kFaultShutdownOpen, 0u);
  EXPECT_NE(out.snapshot.state, VcuState::kDrive);
}

// Each cell bound is flagged on its own, and each reaches its own field: at
// rest the firmware's open-circuit estimate seeds from them.
TEST(SilRig, CellBoundsArriveSeparately) {
  Rig rig;
  RigInputs in = ParkedCar();
  in.min_cell_v = -0.5f;
  EXPECT_EQ(rig.Advance(in, 3).saturated,
            static_cast<uint32_t>(kSatCellVoltage));
  in = ParkedCar();
  in.max_cell_v = 7.0f;
  EXPECT_EQ(rig.Advance(in, 3).saturated,
            static_cast<uint32_t>(kSatCellVoltage));

  Rig rested;
  in = ParkedCar();
  in.min_cell_v = 3.9f;
  in.max_cell_v = 4.0f;
  const RigOutputs out = Hold(rested, in, 1200);  // rest_time_ms is 1000
  EXPECT_NEAR(out.snapshot.battery.ocv_min_cell_v, 3.9f, 1e-4f);
  EXPECT_NEAR(out.snapshot.battery.ocv_max_cell_v, 4.0f, 1e-4f);
}

// BSE3 carries the rear pressure over the whole BSE range.
TEST(SilRig, Bse3ReadsBackAtFullScale) {
  Rig rig;
  RigInputs in = ParkedCar();
  in.brake_front_psi = 0.0f;
  in.brake_rear_psi = 3000.0f;
  const RigOutputs out = Hold(rig, in, 30);
  EXPECT_NEAR(out.snapshot.bse3_psi, 3000.0f, 0.05f);
  EXPECT_NEAR(out.snapshot.pedals.bse2_psi, 3000.0f, 1.0f);
}

// linelock_out is Linelock_Ctrl AND NOT Hard_braking on the board. Each
// pass sets it from the previous Step()'s Linelock_Ctrl, so a request written
// before a 1 ms advance is what the gate sees.
TEST(SilRig, LineLockReadbackIsGatedByHardBraking) {
  Rig rig;
  RigInputs in = ParkedCar();
  bench::Bench& b = rig.bench();
  in.brake_front_psi = in.brake_rear_psi = 100.0f;
  b.linelock_ctrl.Write(true);
  rig.Advance(in, 1);
  EXPECT_FALSE(b.hard_braking.Read());
  EXPECT_TRUE(b.linelock_out.Read());
  in.brake_front_psi = in.brake_rear_psi = 1500.0f;
  b.linelock_ctrl.Write(true);
  rig.Advance(in, 1);
  EXPECT_TRUE(b.hard_braking.Read());
  EXPECT_FALSE(b.linelock_out.Read());
  // With regen off the app never requests it.
  b.linelock_ctrl.Write(false);
  in.brake_front_psi = in.brake_rear_psi = 100.0f;
  Hold(rig, in, 30);
  EXPECT_FALSE(b.linelock_ctrl.Read());
  EXPECT_FALSE(b.linelock_out.Read());
}

}  // namespace
}  // namespace vcu::sil
