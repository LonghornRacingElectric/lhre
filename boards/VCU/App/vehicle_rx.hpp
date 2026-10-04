#pragma once

#include <cstdint>

#include "can_inbox.hpp"
#include "lhre_can_dui.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_inverter.hpp"
#include "lhre_can_pdu.hpp"
#include "lhre_can_pi.hpp"

namespace vcu {

// Everything the vehicle image listens to, all on the Critical bus.
struct VehicleRx {
  RxSlot<lhre::can::inverter::InverterMotorPosition> motor_position;
  RxSlot<lhre::can::inverter::InverterCurrent> inverter_current;
  RxSlot<lhre::can::inverter::InverterVoltage> inverter_voltage;
  RxSlot<lhre::can::inverter::InverterInternalStates> inverter_states;
  RxSlot<lhre::can::inverter::InverterFaults> inverter_faults;
  RxSlot<lhre::can::inverter::InverterHighSpeed> inverter_high_speed;
  RxSlot<lhre::can::hvc::HvcContactorStatus> hvc_contactors;
  RxSlot<lhre::can::hvc::HvcIndicators> hvc_indicators;
  RxSlot<lhre::can::hvc::HvcCellLimits> hvc_cell_limits;
  RxSlot<lhre::can::pdu::PduBse3> pdu_bse3;
  RxSlot<lhre::can::dui::DuiDriverInputs> dui_inputs;
  RxSlot<lhre::can::pi::VcuModeCommand> mode_command;  // aperiodic

  // Returns true if the frame was one of ours.
  bool Dispatch(const lhal::CanFrame& f, uint32_t now_ms) {
    return motor_position.TryDecode(f, now_ms) ||
           inverter_current.TryDecode(f, now_ms) ||
           inverter_voltage.TryDecode(f, now_ms) ||
           inverter_states.TryDecode(f, now_ms) ||
           inverter_faults.TryDecode(f, now_ms) ||
           inverter_high_speed.TryDecode(f, now_ms) ||
           hvc_contactors.TryDecode(f, now_ms) ||
           hvc_indicators.TryDecode(f, now_ms) ||
           hvc_cell_limits.TryDecode(f, now_ms) ||
           pdu_bse3.TryDecode(f, now_ms) || dui_inputs.TryDecode(f, now_ms) ||
           mode_command.TryDecode(f, now_ms);
  }

  // Inverter is up: status and fault frames fresh and no POST/run faults.
  // VSM state is not interpreted (TODO(verify) against the Cascadia doc).
  bool InverterFresh(uint32_t now_ms) const {
    return inverter_faults.Fresh(now_ms) && inverter_states.Fresh(now_ms);
  }
  bool InverterFaulted(uint32_t now_ms) const {
    return inverter_faults.Fresh(now_ms) &&
           (inverter_faults.msg.post_faults != 0 ||
            inverter_faults.msg.run_faults != 0);
  }

  // Motor speed from the fast frame, falling back to motor position
  // (lhre-2026 vcu_can.c:389-423). Returns false when both are stale.
  bool MotorRpm(uint32_t now_ms, float* rpm) const {
    if (inverter_high_speed.Fresh(now_ms)) {
      *rpm = static_cast<float>(inverter_high_speed.msg.motor_speed);
      return true;
    }
    if (motor_position.Fresh(now_ms)) {
      *rpm = static_cast<float>(motor_position.msg.motor_speed);
      return true;
    }
    *rpm = 0.0f;
    return false;
  }
};

}  // namespace vcu
