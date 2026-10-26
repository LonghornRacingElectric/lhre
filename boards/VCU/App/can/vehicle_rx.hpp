#pragma once

#include <cstdint>

#include "lhre_can_dui.hpp"
#include "lhre_can_hvc.hpp"
#include "lhre_can_inverter.hpp"
#include "lhre_can_pdu.hpp"
#include "lhre_can_pi.hpp"
#include "lhre_can_rx_vcu.hpp"
#include "lhre_can_usm.hpp"

namespace vcu {

// Vehicle feedback on the Critical bus.
struct VehicleRx : lhre::can::rx::VcuCriticalRx {
  // Require measurement feedback as well as status. TODO(verify): inverter VSM
  // states.
  bool InverterFresh(uint32_t now_ms) const {
    return inverter_faults.Fresh(now_ms) && inverter_details.Fresh(now_ms) &&
           inverter_current.Fresh(now_ms) && inverter_voltage.Fresh(now_ms) &&
           (inverter_speed.Fresh(now_ms) || inverter_status.Fresh(now_ms));
  }
  bool InverterFaulted(uint32_t now_ms) const {
    return inverter_faults.Fresh(now_ms) &&
           (inverter_faults.msg.post_faults != 0 ||
            inverter_faults.msg.run_faults != 0);
  }

  // Motor speed from the fast frame, falling back to motor position
  // (lhre-2026 vcu_can.c:389-423). Returns false when both are stale.
  bool MotorRpm(uint32_t now_ms, float* rpm) const {
    if (inverter_speed.Fresh(now_ms)) {
      *rpm = static_cast<float>(inverter_speed.msg.motor_speed);
      return true;
    }
    if (inverter_status.Fresh(now_ms)) {
      *rpm = static_cast<float>(inverter_status.msg.motor_speed);
      return true;
    }
    *rpm = 0.0f;
    return false;
  }
};

// Wheel measurements and TSM coolant temps arrive on Data, not Critical.
struct WheelRx : lhre::can::rx::VcuDataAcqRx {
  bool Fresh(uint32_t now) const {
    return acceleration_vector_unsprung_wheel_speed_fl.Fresh(now) &&
           acceleration_vector_unsprung_wheel_speed_fr.Fresh(now) &&
           acceleration_vector_unsprung_wheel_speed_rl.Fresh(now) &&
           acceleration_vector_unsprung_wheel_speed_rr.Fresh(now);
  }
};

}  // namespace vcu
