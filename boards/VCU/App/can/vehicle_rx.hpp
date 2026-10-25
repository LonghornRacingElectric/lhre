#pragma once

#include <cstdint>

#include "lhre_can_dui.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_inverter.hpp"
#include "lhre_can_pdu.hpp"
#include "lhre_can_pi.hpp"
#include "lhre_can_usm.hpp"
#include "longhorn/can_inbox.hpp"

namespace vcu {

// Vehicle feedback on the Critical bus.
struct VehicleRx {
  longhorn::RxSlot<lhre::can::inverter::InverterMotorPosition> motor_position;
  longhorn::RxSlot<lhre::can::inverter::InverterCurrent> inverter_current;
  longhorn::RxSlot<lhre::can::inverter::InverterVoltage> inverter_voltage;
  longhorn::RxSlot<lhre::can::inverter::InverterInternalStates> inverter_states;
  longhorn::RxSlot<lhre::can::inverter::InverterFaults> inverter_faults;
  longhorn::RxSlot<lhre::can::inverter::InverterHighSpeed> inverter_high_speed;
  longhorn::RxSlot<lhre::can::hvc::HvcContactorStatus> hvc_contactors;
  longhorn::RxSlot<lhre::can::hvc::HvcIndicators> hvc_indicators;
  longhorn::RxSlot<lhre::can::hvc::HvcCellLimits> hvc_cell_limits;
  longhorn::RxSlot<lhre::can::pdu::PduBse3> pdu_bse3;
  longhorn::RxSlot<lhre::can::dui::DuiDriverInputs> dui_inputs;
  longhorn::RxSlot<lhre::can::pi::VcuModeCommand> mode_command;  // aperiodic

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

  // Require measurement feedback as well as status. TODO(verify): inverter VSM
  // states.
  bool InverterFresh(uint32_t now_ms) const {
    return inverter_faults.Fresh(now_ms) && inverter_states.Fresh(now_ms) &&
           inverter_current.Fresh(now_ms) && inverter_voltage.Fresh(now_ms) &&
           (inverter_high_speed.Fresh(now_ms) || motor_position.Fresh(now_ms));
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

// Wheel measurements arrive on Data, not Critical.
struct WheelRx {
  longhorn::RxSlot<lhre::can::usm::AccelerationVectorUnsprungWheelSpeedFl> fl;
  longhorn::RxSlot<lhre::can::usm::AccelerationVectorUnsprungWheelSpeedFr> fr;
  longhorn::RxSlot<lhre::can::usm::AccelerationVectorUnsprungWheelSpeedRl> rl;
  longhorn::RxSlot<lhre::can::usm::AccelerationVectorUnsprungWheelSpeedRr> rr;

  bool Dispatch(const lhal::CanFrame& f, uint32_t now) {
    return fl.TryDecode(f, now) || fr.TryDecode(f, now) ||
           rl.TryDecode(f, now) || rr.TryDecode(f, now);
  }
  bool Fresh(uint32_t now) const {
    return fl.Fresh(now) && fr.Fresh(now) && rl.Fresh(now) && rr.Fresh(now);
  }
};

}  // namespace vcu
