// Critical-bus frames: what the inverter, dash and logger actually receive.
// Each frame is packed and unpacked through the generated CAN code, so these
// pin the wire values (scaling, clamping, sign, bit mapping), not just the
// struct fields.

#include "can/critical_frames.hpp"

#include <gtest/gtest.h>

#include <cstdint>

#include "can/field_ranges.hpp"
#include "config/params.hpp"
#include "lhre_can_vcu.hpp"
#include "safety/faults.hpp"

namespace {

using lhre::can::EventMode;
namespace msg = lhre::can::vcu;

template <class Message>
Message OverTheWire(const Message& m) {
  return Message::FromFrame(m.ToFrame());
}

const vcu::InverterParams& Inverter(EventMode mode) {
  return vcu::ParamsFor(mode).inverter;
}

TEST(TorqueCommandFrame, CarriesTheCommandAndEnable) {
  vcu::ControlSnapshot s;
  s.torque_command = 123.4f;
  s.inverter_enable = true;
  const auto cmd =
      OverTheWire(vcu::TorqueCommandFrame(s, Inverter(EventMode::kEndurance)));
  EXPECT_NEAR(cmd.torque_request(), 123.4f, 0.05f);
  EXPECT_TRUE(cmd.enable);
  EXPECT_TRUE(cmd.direction);  // forward, always
  EXPECT_EQ(cmd.rpm_request, 0);
  EXPECT_NEAR(cmd.torque_limit(), 230.0f, 0.05f);
}

// Outside DRIVE the inverter must see enable=0 and no torque.
TEST(TorqueCommandFrame, DisabledCommandIsZero) {
  vcu::ControlSnapshot s;
  const auto cmd =
      OverTheWire(vcu::TorqueCommandFrame(s, Inverter(EventMode::kEndurance)));
  EXPECT_FALSE(cmd.enable);
  EXPECT_EQ(cmd.torque_request_raw, 0);
}

// Regen is negative torque; it must not come out positive.
TEST(TorqueCommandFrame, NegativeTorqueKeepsItsSign) {
  vcu::ControlSnapshot s;
  s.torque_command = -45.6f;
  const auto cmd =
      OverTheWire(vcu::TorqueCommandFrame(s, Inverter(EventMode::kEndurance)));
  EXPECT_NEAR(cmd.torque_request(), -45.6f, 0.05f);
}

// The field is s16 x 0.1 Nm. Unclamped, 4000 Nm would wrap to a large
// negative request.
TEST(TorqueCommandFrame, OutOfRangeTorqueSaturatesInsteadOfWrapping) {
  vcu::ControlSnapshot s;
  s.torque_command = 4000.0f;
  auto cmd =
      OverTheWire(vcu::TorqueCommandFrame(s, Inverter(EventMode::kEndurance)));
  EXPECT_NEAR(cmd.torque_request(), vcu::kS16Nm, 0.05f);
  s.torque_command = -4000.0f;
  cmd =
      OverTheWire(vcu::TorqueCommandFrame(s, Inverter(EventMode::kEndurance)));
  EXPECT_NEAR(cmd.torque_request(), -vcu::kS16Nm, 0.05f);
}

// The inverter enforces the mode's torque limit on its own side, so the
// motor spin cap holds even if the VCU's torque path were wrong.
TEST(TorqueCommandFrame, MotorSpinSendsItsLowTorqueLimit) {
  vcu::ControlSnapshot s;
  const auto cmd =
      OverTheWire(vcu::TorqueCommandFrame(s, Inverter(EventMode::kMotorSpin)));
  EXPECT_NEAR(cmd.torque_limit(), 20.0f, 0.05f);
}

TEST(TorqueCommandFrame, TorqueLimitIsNeverNegativeOrWrapped) {
  vcu::ControlSnapshot s;
  vcu::InverterParams p;
  p.torque_limit_nm = -5.0f;
  EXPECT_EQ(OverTheWire(vcu::TorqueCommandFrame(s, p)).torque_limit_raw, 0);
  p.torque_limit_nm = 5000.0f;
  EXPECT_NEAR(OverTheWire(vcu::TorqueCommandFrame(s, p)).torque_limit(),
              vcu::kS16Nm, 0.05f);
}

TEST(FaultClearFrame, WritesZeroToTheFaultClearAddress) {
  const auto clear =
      OverTheWire(vcu::FaultClearFrame(Inverter(EventMode::kEndurance)));
  EXPECT_EQ(clear.parameter_address, 20);
  EXPECT_TRUE(clear.r_w);
  EXPECT_EQ(clear.data_to_be_written, 0);
}

// Each FaultBit must land on its own field and no other. A swapped pair
// here shows the dash and the logs the wrong fault.
TEST(FaultsFrame, EachFaultBitSetsExactlyItsOwnField) {
  struct Case {
    uint32_t bit;
    bool msg::VcuFaults::*field;
  };
  const Case cases[] = {
      {vcu::kFaultAppsImplausible,
       &msg::VcuFaults::active_faults_apps_implausible},
      {vcu::kFaultApps1Range, &msg::VcuFaults::active_faults_apps1_range},
      {vcu::kFaultApps2Range, &msg::VcuFaults::active_faults_apps2_range},
      {vcu::kFaultBse1Range, &msg::VcuFaults::active_faults_bse1_range},
      {vcu::kFaultBse2Range, &msg::VcuFaults::active_faults_bse2_range},
      {vcu::kFaultSteeringRange, &msg::VcuFaults::active_faults_steering_range},
      {vcu::kFaultBrakeFlagLatched,
       &msg::VcuFaults::active_faults_brake_flag_latched},
      {vcu::kFaultBrakeOverThrottle,
       &msg::VcuFaults::active_faults_brake_over_throttle},
      {vcu::kFaultShutdownOpen, &msg::VcuFaults::active_faults_shutdown_open},
      {vcu::kFaultHvcTimeout, &msg::VcuFaults::active_faults_hvc_timeout},
      {vcu::kFaultInverterTimeout,
       &msg::VcuFaults::active_faults_inverter_timeout},
      {vcu::kFaultPduTimeout, &msg::VcuFaults::active_faults_pdu_timeout},
      {vcu::kFaultInverterFault, &msg::VcuFaults::active_faults_inverter_fault},
      {vcu::kFaultBspdTripped, &msg::VcuFaults::active_faults_bspd_tripped},
      {vcu::kFaultHardBrakeMismatch,
       &msg::VcuFaults::active_faults_hard_brake_mismatch},
      {vcu::kFaultDuiTimeout, &msg::VcuFaults::active_faults_dui_timeout},
      {vcu::kFaultBse3Unavailable,
       &msg::VcuFaults::active_faults_bse3_unavailable},
      {vcu::kFaultContactorsOpen,
       &msg::VcuFaults::active_faults_contactors_open},
      {vcu::kFaultMotorDisconnect,
       &msg::VcuFaults::active_faults_motor_disconnect},
      {vcu::kFaultShutdownSenseImplausible,
       &msg::VcuFaults::active_faults_shutdown_sense_implausible},
      {vcu::kFaultAdcStale, &msg::VcuFaults::active_faults_adc_stale},
      {vcu::kFaultCoolantOvertemp,
       &msg::VcuFaults::active_faults_coolant_overtemp},
  };
  for (const Case& c : cases) {
    vcu::ControlSnapshot s;
    s.faults = c.bit;
    const auto vf = OverTheWire(vcu::FaultsFrame(s));
    for (const Case& other : cases) {
      EXPECT_EQ(vf.*other.field, other.bit == c.bit)
          << "fault bit 0x" << std::hex << c.bit << " vs field for 0x"
          << other.bit;
    }
  }
}

TEST(FaultsFrame, CarriesStateAndCounters) {
  vcu::ControlSnapshot s;
  s.state = vcu::VcuState::kDrive;
  s.primary_fault = lhre::can::VcuFaultCode::kNone;
  s.brake_flags_latched = true;
  s.fault_resets = 7;
  const auto vf = OverTheWire(vcu::FaultsFrame(s));
  EXPECT_EQ(vf.state, vcu::VcuState::kDrive);
  EXPECT_EQ(vf.primary_fault, lhre::can::VcuFaultCode::kNone);
  EXPECT_TRUE(vf.brake_flags_latched);
  EXPECT_EQ(vf.fault_resets, 7);
}

TEST(DriveStateFrame, ReportsDriveModeAndEventMode) {
  vcu::ControlSnapshot s;
  s.state = vcu::VcuState::kRtdWait;
  s.event_mode = EventMode::kMotorSpin;
  auto ds = OverTheWire(vcu::DriveStateFrame(s));
  EXPECT_EQ(ds.prndl_state, 0);
  EXPECT_EQ(ds.event_mode, static_cast<uint8_t>(EventMode::kMotorSpin));

  s.state = vcu::VcuState::kDrive;
  ds = OverTheWire(vcu::DriveStateFrame(s));
  EXPECT_EQ(ds.prndl_state, 1);
}

TEST(DriveStateFrame, StateOfChargeStaysInZeroToOneHundred) {
  vcu::ControlSnapshot s;
  s.battery.soe_pct = 57.3f;
  EXPECT_NEAR(OverTheWire(vcu::DriveStateFrame(s)).state_of_charge_estimate(),
              57.3f, 0.05f);
  s.battery.soe_pct = 140.0f;
  EXPECT_NEAR(OverTheWire(vcu::DriveStateFrame(s)).state_of_charge_estimate(),
              100.0f, 0.05f);
  s.battery.soe_pct = -10.0f;
  EXPECT_EQ(OverTheWire(vcu::DriveStateFrame(s)).state_of_charge_estimate_raw,
            0);
}

// The torque path frame is how a log shows which stage limited torque.
TEST(TorquePathFrame, ReportsEachStage) {
  vcu::ControlSnapshot s;
  s.torque.pedal = 150.0f;
  s.torque.derated = 140.0f;
  s.torque.power_limited = 100.0f;
  s.torque.traction = 90.0f;
  const auto tp = OverTheWire(vcu::TorquePathFrame(s));
  EXPECT_NEAR(tp.torque_lookup(), 150.0f, 0.05f);
  EXPECT_NEAR(tp.torque_derated(), 140.0f, 0.05f);
  EXPECT_NEAR(tp.torque_power_limited(), 100.0f, 0.05f);
  EXPECT_NEAR(tp.torque_traction_controlled(), 90.0f, 0.05f);
}

// The fields are unsigned; a negative (regen) stage must read 0, not wrap
// to thousands of Nm.
TEST(TorquePathFrame, NegativeStagesReadZero) {
  vcu::ControlSnapshot s;
  s.torque.pedal = -1.0f;
  s.torque.traction = -30.0f;
  const auto tp = OverTheWire(vcu::TorquePathFrame(s));
  EXPECT_EQ(tp.torque_lookup_raw, 0);
  EXPECT_EQ(tp.torque_traction_controlled_raw, 0);
}

TEST(AppsVoltagesFrame, OverRangeVoltageSaturates) {
  vcu::ControlSnapshot s;
  s.analog.apps1.source_v = 4.5f;
  s.analog.apps2.source_v = 9.0f;  // a shorted sensor on a 12 V rail
  const auto av = OverTheWire(vcu::AppsVoltagesFrame(s));
  EXPECT_NEAR(av.apps1_voltage(), 4.5f, 0.0002f);
  EXPECT_NEAR(av.apps2_voltage(), vcu::kU16Volts, 0.0002f);
}

TEST(AccelPedalFrame, CarriesTravelAndEachFaultFlag) {
  vcu::ControlSnapshot s;
  s.pedals.pedal_travel = 0.42f;
  s.pedals.apps2_disconnect = true;
  s.pedals.apps_implausible = true;
  const auto ap = OverTheWire(vcu::AccelPedalFrame(s));
  EXPECT_NEAR(ap.accelerator_pedal_travel(), 0.42f, 0.0002f);
  EXPECT_FALSE(ap.apps_faults_apps1_disconnect);
  EXPECT_TRUE(ap.apps_faults_apps2_disconnect);
  EXPECT_FALSE(ap.apps_faults_apps1_out_range);
  EXPECT_FALSE(ap.apps_faults_apps2_out_range);
  EXPECT_FALSE(ap.apps_faults_apps_mismatch);
  EXPECT_TRUE(ap.apps_faults_apps_implause);
}

TEST(BrakesFrame, PressuresSaturateAndBiasStaysAFraction) {
  vcu::ControlSnapshot s;
  s.pedals.bse1_psi = 800.0f;
  s.pedals.bse2_psi = -50.0f;  // below the transducer's zero
  s.bse3_psi = 5000.0f;
  s.brake_bias = 1.4f;
  s.pedals.bse1_out_of_range = true;
  const auto br = OverTheWire(vcu::BrakesFrame(s));
  EXPECT_NEAR(br.brake_pressure_front(), 800.0f, 0.05f);
  EXPECT_EQ(br.brake_pressure_rear_pre_lock_raw, 0);
  EXPECT_NEAR(br.brake_pressure_rear_post_lock(), vcu::kU16Psi, 0.05f);
  EXPECT_LE(br.brake_bias(), 1.0f + 1e-4f);
  EXPECT_TRUE(br.bse_faults_bse1_out_range);
  EXPECT_FALSE(br.bse_faults_bse2_out_range);
}

TEST(SteeringFrame, AngleKeepsItsSignAndSaturates) {
  vcu::ControlSnapshot s;
  s.steering.angle_deg = -37.5f;
  EXPECT_NEAR(OverTheWire(vcu::SteeringFrame(s)).steering_column_angle(),
              -37.5f, 0.004f);
  s.steering.angle_deg = 400.0f;
  EXPECT_NEAR(OverTheWire(vcu::SteeringFrame(s)).steering_column_angle(),
              vcu::kS16Deg, 0.004f);
}

TEST(EnergyFrame, NegativeEnergyReadsZero) {
  vcu::ControlSnapshot s;
  s.battery.net_energy_wh = -12.0;
  s.battery.regen_energy_wh = 3.5;
  const auto en = OverTheWire(vcu::EnergyFrame(s));
  EXPECT_EQ(en.net_energy_raw, 0u);
  EXPECT_NEAR(en.regen_energy(), 3.5f, 0.001f);
}

}  // namespace
