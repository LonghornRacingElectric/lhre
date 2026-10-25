#include "app/vcu_app.hpp"
#include "can/critical_frames.hpp"
#include "can/send.hpp"
#include "lhre_can_vcu.hpp"
#include "longhorn/period.hpp"

namespace vcu {

namespace msg = lhre::can::vcu;
using longhorn::PeriodMs;

void App::SendCritical(uint32_t now) {
  const Params& P = *params_;
  lhal::CanBus* bus = p_.critical_can;
  if (bus == nullptr) {
    return;
  }
  const ControlSnapshot& s = live_;

  // Inverter command every tick (333 Hz). enable=0 outside DRIVE also
  // satisfies the inverter's disable-before-enable lockout.
  Send(bus, TorqueCommandFrame(s, P.inverter).ToFrame());

  // One-shot inverter fault clear after boot (lhre-2026 vcu_can.c:234-244).
  if (!fault_clear_sent_ &&
      lhal::ElapsedMs(now, boot_ms_, P.inverter.fault_clear_delay_ms)) {
    fault_clear_sent_ = Send(bus, FaultClearFrame(P.inverter).ToFrame()) == 1;
  }

  Send(bus, AppsVoltagesFrame(s).ToFrame());
  Send(bus, AccelPedalFrame(s).ToFrame());
  Send(bus, BrakePedalFrame(s).ToFrame());
  Send(bus, BseVoltagesFrame(s).ToFrame());
  Send(bus, BrakesFrame(s).ToFrame());
  Send(bus, SteeringFrame(s).ToFrame());
  Send(bus, DriveStateFrame(s).ToFrame());

  if (status_gate_.Due(now, PeriodMs<msg::VcuStatus>())) {
    Send(bus, StatusFrame(s).ToFrame());
  }
  if (faults_gate_.Due(now, PeriodMs<msg::VcuFaults>())) {
    Send(bus, FaultsFrame(s).ToFrame());
  }
  if (energy_gate_.Due(now, PeriodMs<msg::VcuEnergy>())) {
    Send(bus, EnergyFrame(s).ToFrame());
    Send(bus, TorquePathFrame(s).ToFrame());
  }
}

}  // namespace vcu
