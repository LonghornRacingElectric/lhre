#include "Sil/rig.hpp"

#include <vector>

#include "Sil/sensors.hpp"
#include "app/vcu_app.hpp"
#include "io/analog.hpp"
#include "lhre_can_dui.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_inverter.hpp"
#include "lhre_can_pdu.hpp"
#include "lhre_can_usm.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/period.hpp"
#include "test/bench.hpp"

namespace vcu::sil {

namespace {

namespace can = lhre::can;

// Every periodic frame the VCU reads from the rest of the car. Fields the VCU
// does not read stay zero.
struct CarFrames {
  can::hvc::ContactorStatus contactors;
  can::hvc::BatteryCellLimits cells;
  can::hvc::IndicatorsShutdownStatus indicators;
  can::inverter::InverterFaults inverter_faults;
  can::inverter::InverterDetails inverter_details;
  can::inverter::InverterSpeed inverter_speed;
  can::inverter::InverterVoltage inverter_voltage;
  can::inverter::InverterCurrent inverter_current;
  can::pdu::Bse3 bse3;
  can::dui::DuiDriverInputs dui;
  can::usm::AccelerationVectorUnsprungWheelSpeedFl wheel_fl;
  can::usm::AccelerationVectorUnsprungWheelSpeedFr wheel_fr;
  can::usm::AccelerationVectorUnsprungWheelSpeedRl wheel_rl;
  can::usm::AccelerationVectorUnsprungWheelSpeedRr wheel_rr;
};

void Flag(bool clamped, uint32_t bit, uint32_t* saturated) {
  if (clamped) {
    *saturated |= bit;
  }
}

CarFrames PackCarFrames(const RigInputs& in, uint32_t* saturated) {
  CarFrames f;
  bool c = false;

  f.contactors.hvc_state_machine = static_cast<uint8_t>(in.hvc_state);
  f.contactors.positive_hv_contactor = in.contactors_closed;
  f.contactors.negative_hv_contactor = in.contactors_closed;
  f.cells.set_min_cell_voltage(FitField(in.min_cell_v, 0, 65535, 0.0001f, &c));
  Flag(c, kSatCellVoltage, saturated);
  f.cells.set_max_cell_voltage(FitField(in.max_cell_v, 0, 65535, 0.0001f, &c));
  Flag(c, kSatCellVoltage, saturated);

  f.inverter_speed.motor_speed = MotorRpm(in.motor_speed_rad_s, &c);
  Flag(c, kSatMotorSpeed, saturated);
  f.inverter_speed.set_torque_feedback(
      FitField(in.motor_torque_nm, -32768, 32767, 0.1f, &c));
  Flag(c, kSatMotorTorque, saturated);
  f.inverter_speed.set_bus_voltage(
      FitField(in.dc_bus_voltage_v, 0, 65535, 0.1f, &c));
  Flag(c, kSatBusVoltage, saturated);
  f.inverter_voltage.set_dc_bus_voltage(
      FitField(in.dc_bus_voltage_v, -32768, 32767, 0.1f, &c));
  Flag(c, kSatBusVoltage, saturated);
  f.inverter_current.set_dc_bus_current(
      FitField(in.dc_bus_current_a, -32768, 32767, 0.1f, &c));
  Flag(c, kSatBusCurrent, saturated);

  // The car model has no line lock, so the pressure after it is BSE2's.
  f.bse3.set_brake_pressure_3(FitField(in.brake_rear_psi, 0, 65535, 0.05f, &c));

  f.wheel_fl.set_wheel_speed(WheelSpeed(in.wheel_speed_rad_s[0], &c));
  Flag(c, kSatWheelSpeed, saturated);
  f.wheel_fr.set_wheel_speed(WheelSpeed(in.wheel_speed_rad_s[1], &c));
  Flag(c, kSatWheelSpeed, saturated);
  f.wheel_rl.set_wheel_speed(WheelSpeed(in.wheel_speed_rad_s[2], &c));
  Flag(c, kSatWheelSpeed, saturated);
  f.wheel_rr.set_wheel_speed(WheelSpeed(in.wheel_speed_rad_s[3], &c));
  Flag(c, kSatWheelSpeed, saturated);
  return f;
}

// Each node transmits at its spec rate, every message in phase at t = 0.
template <class Message>
void SendIfDue(lhal::host::Can& node, uint32_t now_ms, const Message& m) {
  if (now_ms % longhorn::PeriodMs<Message>() == 0) {
    node.Send(m.ToFrame());
  }
}

void SendDueFrames(bench::Bench& b, uint32_t now_ms, const CarFrames& f) {
  SendIfDue(b.car, now_ms, f.contactors);
  SendIfDue(b.car, now_ms, f.cells);
  SendIfDue(b.car, now_ms, f.indicators);
  SendIfDue(b.car, now_ms, f.inverter_faults);
  SendIfDue(b.car, now_ms, f.inverter_details);
  SendIfDue(b.car, now_ms, f.inverter_speed);
  SendIfDue(b.car, now_ms, f.inverter_voltage);
  SendIfDue(b.car, now_ms, f.inverter_current);
  SendIfDue(b.car, now_ms, f.bse3);
  SendIfDue(b.car, now_ms, f.dui);
  SendIfDue(b.logger, now_ms, f.wheel_fl);
  SendIfDue(b.logger, now_ms, f.wheel_fr);
  SendIfDue(b.logger, now_ms, f.wheel_rl);
  SendIfDue(b.logger, now_ms, f.wheel_rr);
}

void ApplyAnalogAndPins(bench::Bench& b, const RigInputs& in,
                        uint32_t* saturated) {
  const Params& p = b.params;
  b.SetPedalTravel(in.accelerator_travel);

  b.SetBrakes(BseVolts(in.brake_front_psi, p.bse),
              BseVolts(in.brake_rear_psi, p.bse));

  bool c = false;
  b.SetSteering(SteeringVolts(in.steering_angle_deg, p.steering, &c));
  Flag(c, kSatSteering, saturated);

  b.SetLoop(in.shutdown_loop_v, in.shutdown_loop_v);
  b.PressRtd(in.rtd_pressed);

  // The BSPD's hard-brake comparators, trimmed to the firmware's threshold
  // and fed the pin volts the firmware computes from the same ADC counts,
  // so its cross-check agrees with them even at the threshold.
  const AnalogReadings a =
      ReadAnalog(&b.adc1, &b.adc2, bench::kVrefintCal, p.analog);
  b.hard_braking.Write(a.bse1.pin_v > p.bse.hard_brake_pin_v &&
                       a.bse2.pin_v > p.bse.hard_brake_pin_v);
}

}  // namespace

Rig::Rig(lhre::can::EventMode mode) : bench_(std::make_unique<bench::Bench>()) {
  bench_->params = ParamsFor(mode);
  // No USB host is plugged in while the car drives.
  bench_->usb.set_connected(false);
  app_ = std::make_unique<App>(bench_->peripherals(), &bench_->params);
}

Rig::~Rig() = default;

RigOutputs Rig::Advance(const RigInputs& in, uint32_t ms) {
  bench::Bench& b = *bench_;
  RigOutputs out;
  ApplyAnalogAndPins(b, in, &out.saturated);
  const CarFrames frames = PackCarFrames(in, &out.saturated);
  for (uint32_t i = 0; i < ms; ++i) {
    const uint32_t now = b.clock.Millis();
    b.adc1.CompleteScan();
    b.adc2.CompleteScan();
    SendDueFrames(b, now, frames);
    // linelock_out is a gate on the board, Linelock_Ctrl AND NOT Hard_braking
    // (README.md, pin table), not valve feedback.
    b.linelock_out.Write(b.linelock_ctrl.Read() && !b.hard_braking.Read());
    app_->Step();
    Drain(&out);
    b.clock.Advance(1);
  }
  out.now_ms = b.clock.Millis();
  out.snapshot = app_->snapshot();
  return out;
}

void Rig::Drain(RigOutputs* out) {
  using Command = can::vcu::InverterTorqueCommand;
  bench::Bench& b = *bench_;
  lhal::CanFrame f;
  while (b.car.Receive(&f)) {
    if (Command::Matches(f.id) && f.len >= Command::kDlc) {
      const Command c = Command::FromFrame(f);
      out->command.enable = c.enable;
      out->command.direction = c.direction;
      out->command.torque_request_nm = c.torque_request();
      out->command.torque_limit_nm = c.torque_limit();
      ++out->torque_frames;
    }
  }
  while (b.logger.Receive(&f)) {
  }
  // The host fakes keep every frame and byte; a long run would grow
  // without bound.
  b.vcu_critical.ClearSent();
  b.vcu_data.ClearSent();
  b.car.ClearSent();
  b.logger.ClearSent();
  const std::vector<uint8_t> text = b.vcp.TakeTx();
  console_.append(text.begin(), text.end());
  if (console_.size() > kConsoleLimit) {
    const size_t drop = console_.size() - kConsoleLimit;
    console_.erase(0, drop);
    console_dropped_ += drop;
  }
  b.usb.TakeTx();
}

std::string Rig::TakeConsole() {
  std::string out;
  out.swap(console_);
  return out;
}

}  // namespace vcu::sil
