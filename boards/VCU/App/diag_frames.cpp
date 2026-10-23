#include "diag_frames.hpp"

#include "lhre_can_vcu.hpp"

namespace vcu {
namespace {

using namespace lhre::can::vcu;  // NOLINT: generated message structs

// Representable ranges of the fields below (spec bit widths x scale).
constexpr float kU16Volts = 6.5535f;      // u16 x 0.0001 V
constexpr float kU16LoopVolts = 655.35f;  // u16 x 0.01 V
constexpr float kS16Fraction = 3.2767f;   // s16 x 0.0001
constexpr float kS16Psi = 3276.7f;        // s16 x 0.1 psi
constexpr float kS16Deg = 131.068f;       // s16 x 0.004 deg

int Send(lhal::CanBus* bus, const lhal::CanFrame& frame) {
  return lhal::IsOk(bus->Send(frame)) ? 1 : 0;
}

}  // namespace

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
  va.set_apps1(ClampPhys(a.apps1.pin_v, 0.0f, kU16Volts));
  va.set_apps2(ClampPhys(a.apps2.pin_v, 0.0f, kU16Volts));
  va.set_bse1(ClampPhys(a.bse1.pin_v, 0.0f, kU16Volts));
  va.set_bse2(ClampPhys(a.bse2.pin_v, 0.0f, kU16Volts));
  sent += Send(bus, va.ToFrame());

  VcuAdcVoltsB vb;
  vb.set_steering(ClampPhys(a.steering.pin_v, 0.0f, kU16Volts));
  vb.set_shutdown_in_loop(
      ClampPhys(a.shutdown_in.source_v, 0.0f, kU16LoopVolts));
  vb.set_shutdown_out_loop(
      ClampPhys(a.shutdown_out.source_v, 0.0f, kU16LoopVolts));
  vb.set_vdda(ClampPhys(a.vdda, 0.0f, kU16Volts));
  sent += Send(bus, vb.ToFrame());

  VcuCalibratedA ka;
  ka.set_apps1_travel(ClampPhys(cal.apps1_travel, -kS16Fraction, kS16Fraction));
  ka.set_apps2_travel(ClampPhys(cal.apps2_travel, -kS16Fraction, kS16Fraction));
  ka.set_bse1_pressure(ClampPhys(cal.bse1_psi, -kS16Psi, kS16Psi));
  ka.set_bse2_pressure(ClampPhys(cal.bse2_psi, -kS16Psi, kS16Psi));
  sent += Send(bus, ka.ToFrame());

  VcuCalibratedB kb;
  kb.set_steering_angle(ClampPhys(cal.steering_angle_deg, -kS16Deg, kS16Deg));
  kb.set_steering_travel(
      ClampPhys(cal.steering_travel, -kS16Fraction, kS16Fraction));
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
