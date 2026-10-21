#pragma once

#include <cstdint>

#include "longhorn/debounce.hpp"
#include "vcu_config.hpp"

namespace vcu {

struct PedalInputs {
  // Sensor-side volts (after undoing the board divider).
  float apps1_v = 0.0f;
  float apps2_v = 0.0f;
  float bse1_v = 0.0f;
  float bse2_v = 0.0f;
  // MCU pin volts on the BSE channels, for the hard-brake cross-check.
  float bse1_pin_v = 0.0f;
  float bse2_pin_v = 0.0f;
  // Hard_braking_mcu from the BSPD comparators.
  bool hard_braking_hw = false;
};

struct PedalOutputs {
  // APPS
  float apps1_travel_unclamped = 0.0f;
  float apps2_travel_unclamped = 0.0f;
  float apps1_travel = 0.0f;  // clamped 0..1
  float apps2_travel = 0.0f;
  float pedal_travel = 0.0f;      // filtered + deadzone: the torque input
  bool apps1_disconnect = false;  // below window (instantaneous)
  bool apps2_disconnect = false;
  bool apps1_out_of_range = false;  // above window (instantaneous)
  bool apps2_out_of_range = false;
  bool apps_mismatch = false;     // deviation over threshold (instantaneous)
  bool apps_implausible = false;  // T.4.2: persisted > implausible_ms
  // BSE
  float bse1_psi_unclamped = 0.0f;
  float bse2_psi_unclamped = 0.0f;
  float bse1_psi = 0.0f;  // clamped 0..full scale
  float bse2_psi = 0.0f;
  float bse_psi_filtered = 0.0f;
  bool bse1_disconnect = false;
  bool bse2_disconnect = false;
  bool bse1_out_of_range = false;
  bool bse2_out_of_range = false;
  bool bse1_fault = false;  // range fault persisted > range_fault_ms
  bool bse2_fault = false;
  bool brake_pressed = false;  // 300/250 psi hysteresis
  float brake_light = 0.0f;    // duty for the PDU
  bool brake_for_rtd = false;  // filtered >= brake_light_psi
  bool brake_over_throttle = false;
  // Cross-check
  bool firmware_hard_brake = false;
  bool hard_brake_mismatch = false;
};

// APPS and BSE processing, called once per control tick.
class PedalProcessor {
 public:
  PedalOutputs Update(const PedalInputs& in, uint32_t now_ms,
                      const AppsParams& apps, const BseParams& bse);

 private:
  longhorn::PersistenceTimer apps_implausible_timer_;
  longhorn::PersistenceTimer bse1_fault_timer_;
  longhorn::PersistenceTimer bse2_fault_timer_;
  longhorn::PersistenceTimer hard_brake_mismatch_timer_;
  longhorn::Ema pedal_filter_;
  longhorn::Ema bse_filter_;
  bool brake_pressed_ = false;
  bool bot_latched_ = false;
};

}  // namespace vcu
