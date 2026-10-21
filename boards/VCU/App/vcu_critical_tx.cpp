#include "vcu_app.hpp"

#include <cstring>

#include "diag_frames.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/console.hpp"
#include "lookup.hpp"
#include "longhorn/rtos_lock.hpp"
#include "status_led.hpp"
#include "longhorn/period.hpp"

namespace vcu {
using lhre::can::EventMode;
using lhre::can::ShutdownLoopState;
using lhre::can::VcuFaultCode;
namespace msg = lhre::can::vcu;
namespace {
// Representable ranges of the Critical-bus fields (spec bit width x scale).
constexpr float kU16Volts = 6.5535f;     // u16 x 0.0001 V
constexpr float kU16Fraction = 6.5535f;  // u16 x 0.0001
constexpr float kU16Psi = 3276.75f;      // u16 x 0.05 psi
constexpr float kU16Nm = 6553.5f;        // u16 x 0.1 Nm
constexpr float kS16Nm = 3276.7f;        // s16 x 0.1 Nm
constexpr float kS16Deg = 131.068f;      // s16 x 0.004 deg
constexpr float kU32Wh = 4294967.0f;     // u32 x 0.001 Wh

}
namespace {
int Send(lhal::CanBus* bus, const lhal::CanFrame& frame) {
  return bus != nullptr && lhal::IsOk(bus->Send(frame)) ? 1 : 0;
}
}  // namespace
namespace {
msg::InverterTorqueCommand MakeInverterTorqueCommand(const ControlSnapshot& s, const Params& P) {
  msg::InverterTorqueCommand cmd;
  cmd.set_torque_request(ClampPhys(s.torque_command, -kS16Nm, kS16Nm));
  cmd.rpm_request = 0;
  cmd.direction = true;
  cmd.enable = s.inverter_enable;
  cmd.set_torque_limit(ClampPhys(P.inverter.torque_limit_nm, 0.0f, kS16Nm));
  return cmd;
}

msg::VcuAppsVoltages MakeVcuAppsVoltages(const ControlSnapshot& s, const Params& /*P*/) {
  const auto& ped = s.pedals;
  const auto& a = s.analog;
  msg::VcuAppsVoltages av;
  av.set_apps1_voltage(ClampPhys(a.apps1.source_v, 0.0f, kU16Volts));
  av.set_apps2_voltage(ClampPhys(a.apps2.source_v, 0.0f, kU16Volts));
  av.set_apps1_travel(ped.apps1_travel);
  av.set_apps2_travel(ped.apps2_travel);
  return av;
}

msg::VcuAccelPedal MakeVcuAccelPedal(const ControlSnapshot& s, const Params& /*P*/) {
  const auto& ped = s.pedals;
  msg::VcuAccelPedal ap;
  ap.set_accel_pedal_travel(ClampPhys(ped.pedal_travel, 0.0f, kU16Fraction));
  ap.apps_s.faultsapps1_disconnect = ped.apps1_disconnect;
  ap.apps_s.faultsapps2_disconnect = ped.apps2_disconnect;
  ap.apps_s.faultsapps1_out_of_range = ped.apps1_out_of_range;
  ap.apps_s.faultsapps2_out_of_range = ped.apps2_out_of_range;
  ap.apps_s.faultsapps_mismatch = ped.apps_mismatch;
  ap.apps_s.faultsapps_implausible = ped.apps_implausible;
  return ap;
}

msg::VcuBrakePedal MakeVcuBrakePedal(const ControlSnapshot& s, const Params& /*P*/) {
  const auto& ped = s.pedals;
  msg::VcuBrakePedal bp;
  bp.set_brake_light(ped.brake_light);
  return bp;
}

msg::VcuBseVoltages MakeVcuBseVoltages(const ControlSnapshot& s, const Params& /*P*/) {
  const auto& a = s.analog;
  msg::VcuBseVoltages bv;
  bv.set_bse_front_voltage(ClampPhys(a.bse1.source_v, 0.0f, kU16Volts));
  bv.set_bse_rear_voltage(ClampPhys(a.bse2.source_v, 0.0f, kU16Volts));
  return bv;
}

msg::VcuBrakes MakeVcuBrakes(const ControlSnapshot& s, const Params& /*P*/) {
  const auto& ped = s.pedals;
  msg::VcuBrakes br;
  br.set_brake_pressure_front(ClampPhys(ped.bse1_psi, 0.0f, kU16Psi));
  br.set_brake_pressure_rear_pre_lock(ClampPhys(ped.bse2_psi, 0.0f, kU16Psi));
  br.set_brake_pressure_rear_post_lock(
      ClampPhys(s.bse3_psi, 0.0f, kU16Psi));
  br.set_brake_bias(ClampPhys(s.brake_bias, 0.0f, 1.0f));
  br.bse_s.faultsbse1_disconnect = ped.bse1_disconnect;
  br.bse_s.faultsbse2_disconnect = ped.bse2_disconnect;
  br.bse_s.faultsbse1_out_of_range = ped.bse1_out_of_range;
  br.bse_s.faultsbse2_out_of_range = ped.bse2_out_of_range;
  return br;
}

msg::VcuSteering MakeVcuSteering(const ControlSnapshot& s, const Params& /*P*/) {
  msg::VcuSteering st;
  st.set_steering_angle(ClampPhys(s.steering.angle_deg, -kS16Deg, kS16Deg));
  return st;
}

msg::VcuDriveState MakeVcuDriveState(const ControlSnapshot& s, const Params& /*P*/) {
  const auto& ped = s.pedals;
  msg::VcuDriveState ds;
  ds.prndl_state = s.state == VcuState::kDrive ? 1 : 0;
  ds.stomp_fault = ped.brake_over_throttle;
  ds.ready_to_drive_buzzer = s.buzzer;
  ds.set_soc_estimate(ClampPhys(s.battery.soe_pct, 0.0f, 100.0f));
  ds.line_lock_enabled = s.inputs.linelock_out;
  ds.event_mode = s.event_mode;
  ds.line_lock_request = s.linelock_request;
  return ds;
}

msg::VcuStatus MakeVcuStatus(const ControlSnapshot& s, const Params& /*P*/) {
  msg::VcuStatus hb;
    hb.state = s.state;
    hb.s.faultssensor_loss =
        (s.faults & (kFaultAppsImplausible | kFaultBse1Range | kFaultBse2Range |
                    kFaultBrakeFlagLatched)) != 0;
    hb.set_torque_request(ClampPhys(s.torque_command, -300.0f, 300.0f));
    hb.set_steering_angle(
        ClampPhys(s.steering.angle_deg, -3276.0f, 3276.0f));
  return hb;
}

msg::VcuFaults MakeVcuFaults(const ControlSnapshot& s, const Params& /*P*/) {
  msg::VcuFaults vf;
    const uint32_t f = s.faults;
    vf.active_s.faultsapps_implausible = (f & kFaultAppsImplausible) != 0;
    vf.active_s.faultsapps1_range = (f & kFaultApps1Range) != 0;
    vf.active_s.faultsapps2_range = (f & kFaultApps2Range) != 0;
    vf.active_s.faultsbse1_range = (f & kFaultBse1Range) != 0;
    vf.active_s.faultsbse2_range = (f & kFaultBse2Range) != 0;
    vf.active_s.faultssteering_range = (f & kFaultSteeringRange) != 0;
    vf.active_s.faultsbrake_flag_latched = (f & kFaultBrakeFlagLatched) != 0;
    vf.active_s.faultsbrake_over_throttle = (f & kFaultBrakeOverThrottle) != 0;
    vf.active_s.faultsshutdown_open = (f & kFaultShutdownOpen) != 0;
    vf.active_s.faultshvc_timeout = (f & kFaultHvcTimeout) != 0;
    vf.active_s.faultsinverter_timeout = (f & kFaultInverterTimeout) != 0;
    vf.active_s.faultspdu_timeout = (f & kFaultPduTimeout) != 0;
    vf.active_s.faultsinverter_fault = (f & kFaultInverterFault) != 0;
    vf.active_s.faultsbspd_tripped = (f & kFaultBspdTripped) != 0;
    vf.active_s.faultshard_brake_mismatch = (f & kFaultHardBrakeMismatch) != 0;
    vf.active_s.faultsdui_timeout = (f & kFaultDuiTimeout) != 0;
    vf.active_s.faultsbse3_unavailable = (f & kFaultBse3Unavailable) != 0;
    vf.active_s.faultscontactors_open = (f & kFaultContactorsOpen) != 0;
    vf.active_s.faultsmotor_disconnect = (f & kFaultMotorDisconnect) != 0;
    vf.active_s.faultsshutdown_sense_implausible =
        (f & kFaultShutdownSenseImplausible) != 0;
    vf.active_s.faultsadc_stale = (f & kFaultAdcStale) != 0;
    vf.primary_fault = PrimaryFault(f);
    vf.state = s.state;
    vf.brake_flags_latched = s.brake_flags_latched;
    vf.fault_resets = s.fault_resets;
  return vf;
}

msg::VcuEnergy MakeVcuEnergy(const ControlSnapshot& s, const Params& /*P*/) {
  msg::VcuEnergy en;
    en.set_net_energy(ClampPhys(static_cast<float>(s.battery.net_energy_wh),
                                0.0f, kU32Wh));
    en.set_regen_energy(ClampPhys(
        static_cast<float>(s.battery.regen_energy_wh), 0.0f, kU32Wh));
  return en;
}

msg::VcuTorquePath MakeVcuTorquePath(const ControlSnapshot& s, const Params& /*P*/) {
  msg::VcuTorquePath tp;
    tp.set_torque_lookup(ClampPhys(s.torque.lookup, 0.0f, kU16Nm));
    tp.set_torque_derated(ClampPhys(s.torque.derated, 0.0f, kU16Nm));
    tp.set_torque_power_limited(
        ClampPhys(s.torque.power_limited, 0.0f, kU16Nm));
    tp.set_torque_final(ClampPhys(s.torque_command, 0.0f, kU16Nm));
  return tp;
}
}
void App::SendCritical(uint32_t now) {
  const Params& P = *params_;
  ControlSnapshot tx = live_;
  tx.state = sm_.state();
  tx.event_mode = params_->event_mode;
  tx.faults = faults_;
  tx.brake_flags_latched = brake_latch_.latched();
  tx.fault_resets = brake_latch_.resets();
  lhal::CanBus* bus = p_.critical_can;
  if (bus == nullptr) {
    return;
  }

  // Inverter command every tick (333 Hz). enable=0 outside DRIVE also
  // satisfies the inverter's disable-before-enable lockout.
  Send(bus, MakeInverterTorqueCommand(tx, P).ToFrame());

  // One-shot inverter fault clear after boot (lhre-2026 vcu_can.c:234-244).
  if (!fault_clear_sent_ &&
      lhal::ElapsedMs(now, boot_ms_, P.inverter.fault_clear_delay_ms)) {
    msg::InverterParameterRequest clear;
    clear.parameter_address = P.inverter.fault_clear_address;
    clear.write = true;
    clear.data = 0;
    fault_clear_sent_ = Send(bus, clear.ToFrame()) == 1;
  }

  // 333 Hz telemetry set, same IDs and layouts as lhre-2026.
  Send(bus, MakeVcuAppsVoltages(tx, P).ToFrame());

  Send(bus, MakeVcuAccelPedal(tx, P).ToFrame());

  Send(bus, MakeVcuBrakePedal(tx, P).ToFrame());

  Send(bus, MakeVcuBseVoltages(tx, P).ToFrame());

  Send(bus, MakeVcuBrakes(tx, P).ToFrame());

  Send(bus, MakeVcuSteering(tx, P).ToFrame());

  Send(bus, MakeVcuDriveState(tx, P).ToFrame());

  // 100 Hz heartbeat.
  if (status_gate_.Due(
          now, longhorn::PeriodMs<msg::VcuStatus>())) {
    Send(bus, MakeVcuStatus(tx, P).ToFrame());
  }

  // 10 Hz: faults to the dash, energy, torque path.
  if (faults_gate_.Due(
          now, longhorn::PeriodMs<msg::VcuFaults>())) {
    Send(bus, MakeVcuFaults(tx, P).ToFrame());
  }
  if (energy_gate_.Due(
          now, longhorn::PeriodMs<msg::VcuEnergy>())) {
    Send(bus, MakeVcuEnergy(tx, P).ToFrame());

    Send(bus, MakeVcuTorquePath(tx, P).ToFrame());
  }
}


}  // namespace vcu
