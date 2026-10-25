#include "safety/faults.hpp"

namespace vcu {

lhre::can::VcuFaultCode PrimaryFault(uint32_t faults) {
  static constexpr uint32_t kPriority[] = {
      kFaultBspdTripped,      kFaultShutdownOpen,      kFaultContactorsOpen,
      kFaultHvcTimeout,       kFaultInverterTimeout,   kFaultInverterFault,
      kFaultPduTimeout,       kFaultMotorDisconnect,   kFaultAdcStale,
      kFaultAppsImplausible,  kFaultBse1Range,         kFaultBse2Range,
      kFaultBrakeFlagLatched, kFaultBrakeOverThrottle,
  };
  for (uint32_t bit : kPriority) {
    if ((faults & bit) != 0) {
      faults = bit;
      break;
    }
  }
  for (uint32_t i = 0; i < 32; ++i) {
    if ((faults & (1u << i)) != 0) {
      return static_cast<lhre::can::VcuFaultCode>(i + 1);
    }
  }
  return lhre::can::VcuFaultCode::kNone;
}

}  // namespace vcu
