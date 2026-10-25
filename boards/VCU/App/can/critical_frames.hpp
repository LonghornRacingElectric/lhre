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
lhre::can::vcu::VcuAppsVoltages AppsVoltagesFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuAccelPedal AccelPedalFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuBrakePedal BrakePedalFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuBseVoltages BseVoltagesFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuBrakes BrakesFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuSteering SteeringFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuDriveState DriveStateFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuStatus StatusFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuFaults FaultsFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuEnergy EnergyFrame(const ControlSnapshot& s);
lhre::can::vcu::VcuTorquePath TorquePathFrame(const ControlSnapshot& s);

}  // namespace vcu
