#include "can/critical_frames.hpp"

#include "can/field_ranges.hpp"
#include "longhorn/math.hpp"
#include "safety/faults.hpp"

namespace vcu {

namespace msg = lhre::can::vcu;
using longhorn::Clamp;

msg::InverterTorqueCommand TorqueCommandFrame(const ControlSnapshot& s,
                                              const InverterParams& p) {
  msg::InverterTorqueCommand cmd;
  cmd.set_torque_request(Clamp(s.torque_command, -kS16Nm, kS16Nm));
  cmd.rpm_request = 0;
  cmd.direction = true;
  cmd.enable = s.inverter_enable;
  cmd.set_torque_limit(Clamp(p.torque_limit_nm, 0.0f, kS16Nm));
  return cmd;
}

msg::InverterParameterRequest FaultClearFrame(const InverterParams& p) {
  msg::InverterParameterRequest clear;
  clear.parameter_address = p.fault_clear_address;
  clear.r_w = true;
  clear.data_to_be_written = 0;
  return clear;
}

msg::AppsVoltages AppsVoltagesFrame(const ControlSnapshot& s) {
  msg::AppsVoltages av;
  av.set_apps1_voltage(Clamp(s.analog.apps1.source_v, 0.0f, kU16Volts));
  av.set_apps2_voltage(Clamp(s.analog.apps2.source_v, 0.0f, kU16Volts));
  av.set_apps1_travel(s.pedals.apps1_travel);
  av.set_apps2_travel(s.pedals.apps2_travel);
  return av;
}

msg::AcceleratorPedal AccelPedalFrame(const ControlSnapshot& s) {
  const PedalOutputs& ped = s.pedals;
  msg::AcceleratorPedal ap;
  ap.set_accelerator_pedal_travel(Clamp(ped.pedal_travel, 0.0f, kU16Fraction));
  ap.apps_faults_apps1_disconnect = ped.apps1_disconnect;
  ap.apps_faults_apps2_disconnect = ped.apps2_disconnect;
  ap.apps_faults_apps1_out_range = ped.apps1_out_of_range;
  ap.apps_faults_apps2_out_range = ped.apps2_out_of_range;
  ap.apps_faults_apps_mismatch = ped.apps_mismatch;
  ap.apps_faults_apps_implause = ped.apps_implausible;
  return ap;
}

msg::BrakePedal BrakePedalFrame(const ControlSnapshot& s) {
  msg::BrakePedal bp;
  bp.set_brake_light_percent(s.pedals.brake_light);
  return bp;
}

msg::BseVoltages BseVoltagesFrame(const ControlSnapshot& s) {
  msg::BseVoltages bv;
  bv.set_bse_front_voltage(Clamp(s.analog.bse1.source_v, 0.0f, kU16Volts));
  bv.set_bse_rear_voltage(Clamp(s.analog.bse2.source_v, 0.0f, kU16Volts));
  return bv;
}

msg::Brakes BrakesFrame(const ControlSnapshot& s) {
  const PedalOutputs& ped = s.pedals;
  msg::Brakes br;
  br.set_brake_pressure_front(Clamp(ped.bse1_psi, 0.0f, kU16Psi));
  br.set_brake_pressure_rear_pre_lock(Clamp(ped.bse2_psi, 0.0f, kU16Psi));
  br.set_brake_pressure_rear_post_lock(Clamp(s.bse3_psi, 0.0f, kU16Psi));
  br.set_brake_bias(Clamp(s.brake_bias, 0.0f, 1.0f));
  br.bse_faults_bse1_disconnect = ped.bse1_disconnect;
  br.bse_faults_bse2_disconnect = ped.bse2_disconnect;
  br.bse_faults_bse1_out_range = ped.bse1_out_of_range;
  br.bse_faults_bse2_out_range = ped.bse2_out_of_range;
  return br;
}

msg::SteeringColumn SteeringFrame(const ControlSnapshot& s) {
  msg::SteeringColumn st;
  st.set_steering_column_angle(Clamp(s.steering.angle_deg, -kS16Deg, kS16Deg));
  return st;
}

msg::VcuState DriveStateFrame(const ControlSnapshot& s) {
  msg::VcuState ds;
  ds.prndl_state = s.state == VcuState::kDrive ? 1 : 0;
  ds.stomp_fault = s.pedals.brake_over_throttle;
  ds.ready_to_drive_buzzer = s.buzzer;
  ds.set_state_of_charge_estimate(Clamp(s.battery.soe_pct, 0.0f, 100.0f));
  ds.line_lock_enabled = s.inputs.linelock_out;
  ds.event_mode = static_cast<uint8_t>(s.event_mode);
  ds.line_lock_request = s.linelock_request;
  return ds;
}

msg::VcuFaults FaultsFrame(const ControlSnapshot& s) {
  msg::VcuFaults vf;
  const uint32_t f = s.faults;
  vf.active_faults_apps_implausible = (f & kFaultAppsImplausible) != 0;
  vf.active_faults_apps1_range = (f & kFaultApps1Range) != 0;
  vf.active_faults_apps2_range = (f & kFaultApps2Range) != 0;
  vf.active_faults_bse1_range = (f & kFaultBse1Range) != 0;
  vf.active_faults_bse2_range = (f & kFaultBse2Range) != 0;
  vf.active_faults_steering_range = (f & kFaultSteeringRange) != 0;
  vf.active_faults_brake_flag_latched = (f & kFaultBrakeFlagLatched) != 0;
  vf.active_faults_brake_over_throttle = (f & kFaultBrakeOverThrottle) != 0;
  vf.active_faults_shutdown_open = (f & kFaultShutdownOpen) != 0;
  vf.active_faults_hvc_timeout = (f & kFaultHvcTimeout) != 0;
  vf.active_faults_inverter_timeout = (f & kFaultInverterTimeout) != 0;
  vf.active_faults_pdu_timeout = (f & kFaultPduTimeout) != 0;
  vf.active_faults_inverter_fault = (f & kFaultInverterFault) != 0;
  vf.active_faults_bspd_tripped = (f & kFaultBspdTripped) != 0;
  vf.active_faults_hard_brake_mismatch = (f & kFaultHardBrakeMismatch) != 0;
  vf.active_faults_dui_timeout = (f & kFaultDuiTimeout) != 0;
  vf.active_faults_bse3_unavailable = (f & kFaultBse3Unavailable) != 0;
  vf.active_faults_contactors_open = (f & kFaultContactorsOpen) != 0;
  vf.active_faults_motor_disconnect = (f & kFaultMotorDisconnect) != 0;
  vf.active_faults_shutdown_sense_implausible =
      (f & kFaultShutdownSenseImplausible) != 0;
  vf.active_faults_adc_stale = (f & kFaultAdcStale) != 0;
  vf.primary_fault = s.primary_fault;
  vf.state = s.state;
  vf.brake_flags_latched = s.brake_flags_latched;
  vf.fault_resets = s.fault_resets;
  return vf;
}

msg::EnergyEstimate EnergyFrame(const ControlSnapshot& s) {
  msg::EnergyEstimate en;
  en.set_net_energy(
      Clamp(static_cast<float>(s.battery.net_energy_wh), 0.0f, kU32Wh));
  en.set_regen_energy(
      Clamp(static_cast<float>(s.battery.regen_energy_wh), 0.0f, kU32Wh));
  return en;
}

msg::TorquePath TorquePathFrame(const ControlSnapshot& s) {
  msg::TorquePath tp;
  tp.set_torque_lookup(Clamp(s.torque.lookup, 0.0f, kU16Nm));
  tp.set_torque_derated(Clamp(s.torque.derated, 0.0f, kU16Nm));
  tp.set_torque_power_limited(Clamp(s.torque.power_limited, 0.0f, kU16Nm));
  tp.set_torque_traction_controlled(Clamp(s.torque.traction, 0.0f, kU16Nm));
  return tp;
}

}  // namespace vcu
