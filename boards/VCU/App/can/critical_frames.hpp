#pragma once

#include "app/control_snapshot.hpp"
#include "config/params.hpp"
#include "lhre_can_vcu.hpp"

namespace vcu {

// Critical-bus frames packed from one control tick, same IDs and layouts as
// lhre-2026.
lhre::can::vcu::InverterTorqueCommand TorqueCommandFrame(
    const ControlSnapshot& s, const InverterParams& p);
lhre::can::vcu::InverterParameterRequest FaultClearFrame(
    const InverterParams& p);
lhre::can::vcu::AppsVoltages AppsVoltagesFrame(const ControlSnapshot& s);
lhre::can::vcu::AcceleratorPedal AccelPedalFrame(const ControlSnapshot& s);
lhre::can::vcu::BrakePedal BrakePedalFrame(const ControlSnapshot& s);
lhre::can::vcu::BseVoltages BseVoltagesFrame(const ControlSnapshot& s);
lhre::can::vcu::Brakes BrakesFrame(const ControlSnapshot& s);
lhre::can::vcu::SteeringColumn SteeringFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuState DriveStateFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuFaults FaultsFrame(const ControlSnapshot& s);
lhre::can::vcu::EnergyEstimate EnergyFrame(const ControlSnapshot& s);
lhre::can::vcu::TorquePath TorquePathFrame(const ControlSnapshot& s);

}  // namespace vcu
