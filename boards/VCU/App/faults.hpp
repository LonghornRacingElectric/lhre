#pragma once

#include <cstdint>

#include "lhre_can_types.hpp"

namespace vcu {

// Bit i matches VcuFaultBits entry i in lib/spec/types.textproto, and
// VcuFaultCode = i + 1.
enum FaultBit : uint32_t {
  kFaultAppsImplausible = 1u << 0,
  kFaultApps1Range = 1u << 1,
  kFaultApps2Range = 1u << 2,
  kFaultBse1Range = 1u << 3,
  kFaultBse2Range = 1u << 4,
  kFaultSteeringRange = 1u << 5,
  kFaultBrakeFlagLatched = 1u << 6,
  kFaultBrakeOverThrottle = 1u << 7,
  kFaultShutdownOpen = 1u << 8,
  kFaultHvcTimeout = 1u << 9,
  kFaultInverterTimeout = 1u << 10,
  kFaultPduTimeout = 1u << 11,
  kFaultInverterFault = 1u << 12,
  kFaultBspdTripped = 1u << 13,
  kFaultHardBrakeMismatch = 1u << 14,
  kFaultDuiTimeout = 1u << 15,
  kFaultBse3Unavailable = 1u << 16,
  kFaultContactorsOpen = 1u << 17,
  kFaultMotorDisconnect = 1u << 18,
  kFaultShutdownSenseImplausible = 1u << 19,
};

// Faults that zero torque and force DRIVE -> FAULT. Brake-over-throttle
// zeroes torque without leaving DRIVE (it clears when the pedal is
// released). The rest are diagnostics.
inline constexpr uint32_t kTorqueCutFaults =
    kFaultAppsImplausible | kFaultBse1Range | kFaultBse2Range |
    kFaultBrakeFlagLatched | kFaultShutdownOpen | kFaultHvcTimeout |
    kFaultInverterTimeout | kFaultPduTimeout | kFaultInverterFault |
    kFaultBspdTripped;

// The fault the dash shows when several are active: what most needs the
// driver's attention first.
lhre::can::VcuFaultCode PrimaryFault(uint32_t faults);

// Soft latch for the board's brake comparator flags (B1/B2 disconnect and
// overvoltage). Any debounced flag sets it; only an explicit driver reset
// from the dash clears it, and only while no flag is active. It survives a
// power cycle through BoardServices::SaveFaultLatch (the caller persists
// when dirty()).
class BrakeFlagLatch {
 public:
  // Persisted encoding: 0 = clear, kPersistedSet = latched.
  static constexpr uint32_t kPersistedSet = 0xB5A7E001u;

  void Restore(uint32_t persisted) { latched_ = persisted == kPersistedSet; }

  // `reset_edge` is a rising edge of the dash's fault_reset.
  void Update(bool any_flag, bool reset_edge) {
    if (any_flag) {
      if (!latched_) {
        dirty_ = true;
      }
      latched_ = true;
    } else if (reset_edge && latched_) {
      latched_ = false;
      dirty_ = true;
      ++resets_;
    }
  }

  bool latched() const { return latched_; }
  uint8_t resets() const { return resets_; }
  bool dirty() const { return dirty_; }
  uint32_t persisted_value() const { return latched_ ? kPersistedSet : 0u; }
  void MarkPersisted() { dirty_ = false; }

 private:
  bool latched_ = false;
  bool dirty_ = false;
  uint8_t resets_ = 0;
};

// BMS/IMD error LEDs. The board lights them by default (pull-up on the FET
// gate) when the MCU is dead; firmware only ever adds indication. A flag
// once seen stays lit until power cycle (the HVC also latches them), and a
// silent or never-heard HVC leaves them lit.
class ErrorLeds {
 public:
  void Update(bool indicators_fresh, bool bms_error, bool imd_error) {
    if (indicators_fresh) {
      bms_latched_ = bms_latched_ || bms_error;
      imd_latched_ = imd_latched_ || imd_error;
    }
    bms_on_ = bms_latched_ || !indicators_fresh;
    imd_on_ = imd_latched_ || !indicators_fresh;
  }
  bool bms_on() const { return bms_on_; }
  bool imd_on() const { return imd_on_; }

 private:
  bool bms_latched_ = false;
  bool imd_latched_ = false;
  bool bms_on_ = true;
  bool imd_on_ = true;
};

}  // namespace vcu
