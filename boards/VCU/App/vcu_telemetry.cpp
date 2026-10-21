#include "vcu_app.hpp"

#include "diag_frames.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/period.hpp"

namespace vcu {
namespace msg = lhre::can::vcu;
// Telemetry

void App::TelemetryTick(uint32_t now) {
  const ControlSnapshot s = snapshot();

  if (diag_fast_gate_.Due(now, longhorn::PeriodMs<msg::VcuAdcCountsA>())) {
    CalibratedValues cal;
    cal.apps1_travel = s.pedals.apps1_travel_unclamped;
    cal.apps2_travel = s.pedals.apps2_travel_unclamped;
    cal.bse1_psi = s.pedals.bse1_psi_unclamped;
    cal.bse2_psi = s.pedals.bse2_psi_unclamped;
    cal.steering_angle_deg = s.steering.angle_deg;
    cal.steering_travel = s.steering.travel;
    SendAnalogDiag(p_.data_can, s.analog, cal);
    SendDigitalDiag(p_.data_can, s.inputs, s.outputs, s.loop,
                    s.pedals.firmware_hard_brake, s.pedals.hard_brake_mismatch);
  }
  if (diag_slow_gate_.Due(now, longhorn::PeriodMs<msg::VcuDiag>())) {
    const lhal::CanStats crit = p_.critical_can != nullptr
                                    ? p_.critical_can->stats()
                                    : lhal::CanStats{};
    const lhal::CanStats data =
        p_.data_can != nullptr ? p_.data_can->stats() : lhal::CanStats{};
    SendHealthDiag(p_.data_can, p_.reset_cause, now, crit, data);
  }

  // Flash writes for the brake latch happen here, off the control loop.
  if (latch_save_pending_.exchange(false) && p_.board != nullptr) {
    p_.board->SaveFaultLatch(latch_save_value_.load());
  }
  telemetry_beats_.fetch_add(1);
}

}  // namespace vcu
