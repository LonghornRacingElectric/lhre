#include "can/diag_frames.hpp"

#include "can/field_ranges.hpp"
#include "can/send.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/math.hpp"

namespace vcu {

using namespace lhre::can::vcu;  // NOLINT: generated message structs

int SendAnalogDiag(lhal::CanBus* bus, const AnalogReadings& a,
                   const CalibratedValues& cal) {
  if (bus == nullptr) {
    return 0;
  }
  int sent = 0;
  VcuAdcCountsA ca;
  ca.apps1 = a.apps1.counts;
  ca.apps2 = a.apps2.counts;
  ca.bse1 = a.bse1.counts;
  ca.bse2 = a.bse2.counts;
  sent += Send(bus, ca.ToFrame());

  VcuAdcCountsB cb;
  cb.steering = a.steering.counts;
  cb.shutdown_in = a.shutdown_in.counts;
  cb.shutdown_out = a.shutdown_out.counts;
  cb.vrefint = a.vrefint_counts;
  sent += Send(bus, cb.ToFrame());

  VcuAdcVoltsA va;
  va.set_apps1(longhorn::Clamp(a.apps1.pin_v, 0.0f, kU16Volts));
  va.set_apps2(longhorn::Clamp(a.apps2.pin_v, 0.0f, kU16Volts));
  va.set_bse1(longhorn::Clamp(a.bse1.pin_v, 0.0f, kU16Volts));
  va.set_bse2(longhorn::Clamp(a.bse2.pin_v, 0.0f, kU16Volts));
  sent += Send(bus, va.ToFrame());

  VcuAdcVoltsB vb;
  vb.set_steering(longhorn::Clamp(a.steering.pin_v, 0.0f, kU16Volts));
  vb.set_shutdown_in_loop(
      longhorn::Clamp(a.shutdown_in.source_v, 0.0f, kU16LoopVolts));
  vb.set_shutdown_out_loop(
      longhorn::Clamp(a.shutdown_out.source_v, 0.0f, kU16LoopVolts));
  vb.set_vdda(longhorn::Clamp(a.vdda, 0.0f, kU16Volts));
  sent += Send(bus, vb.ToFrame());

  VcuCalibratedA ka;
  ka.set_apps1_travel(
      longhorn::Clamp(cal.apps1_travel, -kS16Fraction, kS16Fraction));
  ka.set_apps2_travel(
      longhorn::Clamp(cal.apps2_travel, -kS16Fraction, kS16Fraction));
  ka.set_bse1_pressure(longhorn::Clamp(cal.bse1_psi, -kS16Psi, kS16Psi));
  ka.set_bse2_pressure(longhorn::Clamp(cal.bse2_psi, -kS16Psi, kS16Psi));
  sent += Send(bus, ka.ToFrame());

  VcuCalibratedB kb;
  kb.set_steering_angle(
      longhorn::Clamp(cal.steering_angle_deg, -kS16Deg, kS16Deg));
  kb.set_steering_travel(
      longhorn::Clamp(cal.steering_travel, -kS16Fraction, kS16Fraction));
  sent += Send(bus, kb.ToFrame());
  return sent;
}

int SendDigitalDiag(lhal::CanBus* bus, const DigitalInputs& in,
                    const DigitalOutputs& out,
                    lhre::can::ShutdownLoopState loop, bool firmware_hard_brake,
                    bool hard_brake_mismatch) {
  if (bus == nullptr) {
    return 0;
  }
  VcuDigitalIo io;
  io.inputs_b1_disconnect = in.b1_disconnect;
  io.inputs_b1_overvoltage = in.b1_overvoltage;
  io.inputs_b2_disconnect = in.b2_disconnect;
  io.inputs_b2_overvoltage = in.b2_overvoltage;
  io.inputs_hard_braking = in.hard_braking;
  io.inputs_combined_condition = in.combined_condition;
  io.inputs_fault_confirmed = in.fault_confirmed;
  io.inputs_latch_state = in.latch_state;
  io.inputs_linelock_out = in.linelock_out;
  io.inputs_motor_power = in.motor_power;
  io.inputs_motor_disconnect = in.motor_disconnect;
  io.inputs_rtd_switch = in.rtd_pressed;
  io.outputs_linelock_ctrl = out.linelock_ctrl;
  io.outputs_amp_enable = out.amp_enable;
  io.outputs_bms_led = out.bms_led;
  io.outputs_imd_led = out.imd_led;
  io.outputs_crit_stb = out.crit_stb;
  io.outputs_data_stb = out.data_stb;
  io.shutdown_loop = loop;
  io.firmware_hard_brake = firmware_hard_brake;
  io.hard_brake_mismatch = hard_brake_mismatch;
  return Send(bus, io.ToFrame());
}

int SendHealthDiag(lhal::CanBus* bus, lhal::ResetCause reset_cause,
                   uint32_t uptime_ms, const lhal::CanStats& critical,
                   const lhal::CanStats& data) {
  if (bus == nullptr) {
    return 0;
  }
  VcuDiag d;
  // lhal::ResetCause and the spec's ResetCause share numbering.
  d.reset_cause = static_cast<lhre::can::ResetCause>(reset_cause);
  d.uptime = (uptime_ms / 1000u) & 0xFFFFFFu;
  d.critical_bus_off_count = Saturate8(critical.bus_off_count);
  d.data_bus_off_count = Saturate8(data.bus_off_count);
  d.critical_tx_dropped = Saturate8(critical.tx_dropped);
  d.data_tx_dropped = Saturate8(data.tx_dropped);
  return Send(bus, d.ToFrame());
}

}  // namespace vcu
