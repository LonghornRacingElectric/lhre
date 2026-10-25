#pragma once

#include <cstdint>

#include "lhre_can_vcu_rx.hpp"
#include "longhorn/can_inbox.hpp"

namespace vcu {

// Critical-bus feedback. The message slots and Dispatch() are generated
// from the spec's to_boards lists.
struct VehicleRx : lhre::can::vcu::CriticalRx<longhorn::RxSlot> {
  // Require measurement feedback as well as status. TODO(verify): inverter VSM
  // states.
  bool InverterFresh(uint32_t now_ms) const {
    return inverter_faults.Fresh(now_ms) &&
           inverter_internal_states.Fresh(now_ms) &&
           inverter_current.Fresh(now_ms) && inverter_voltage.Fresh(now_ms) &&
           (inverter_high_speed.Fresh(now_ms) ||
            inverter_motor_position.Fresh(now_ms));
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
    if (inverter_motor_position.Fresh(now_ms)) {
      *rpm = static_cast<float>(inverter_motor_position.msg.motor_speed);
      return true;
    }
    *rpm = 0.0f;
    return false;
  }
};

// Data-bus messages: wheel speeds, plus bench frames the vehicle image
// ignores.
struct WheelRx : lhre::can::vcu::DataRx<longhorn::RxSlot> {
  bool Fresh(uint32_t now) const {
    return acceleration_vector_unsprung_wheel_speed_fl.Fresh(now) &&
           acceleration_vector_unsprung_wheel_speed_fr.Fresh(now) &&
           acceleration_vector_unsprung_wheel_speed_rl.Fresh(now) &&
           acceleration_vector_unsprung_wheel_speed_rr.Fresh(now);
  }
};

}  // namespace vcu
