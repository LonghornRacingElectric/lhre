#pragma once

#include <cstddef>
#include <cstdint>

#include "lhre_can_types.hpp"

namespace vcu {

using lhre::can::VcuState;

// Everything the state machine decides on, sampled once per control tick.
struct StateInputs {
  bool bspd_latched = false;  // latch_state_mcu
  bool hvc_fresh = false;     // HVC_CONTACTOR_STATUS within timeout
  lhre::can::HvcState hvc_state = lhre::can::HvcState::kNotEnergized;
  bool contactors_closed = false;  // AIR+ and AIR- feedback
  bool loop_closed = false;        // shutdown sense CLOSED
  bool torque_cut_fault = false;   // any kTorqueCutFaults bit
  bool inverter_ready = false;     // fresh, no POST/run faults
  bool pdu_fresh = false;          // PDU_BSE3 within timeout
  bool bse_valid = false;          // no BSE range fault
  bool brake_for_rtd = false;      // brake pressure >= RTD threshold
  bool pedal_released = false;     // APPS at or below the RTD threshold
  bool rtd_edge = false;           // debounced press edge this tick
  bool rtd_pressed = false;        // debounced level
  bool rtd_release_exits_drive = true;
};

// Derived conditions, named so the transition table reads like the spec.
struct StateConditions {
  // HVC reports precharge done and both AIRs closed.
  static bool Energized(const StateInputs& in) {
    return in.hvc_fresh && in.hvc_state == lhre::can::HvcState::kEnergized &&
           in.contactors_closed;
  }
  static bool Precharging(const StateInputs& in) {
    return in.hvc_fresh && in.hvc_state == lhre::can::HvcState::kPrecharging;
  }
  // Everything DRIVE needs except the driver's RTD action.
  static bool ReadyForRtd(const StateInputs& in) {
    return Energized(in) && in.loop_closed && !in.torque_cut_fault &&
           in.inverter_ready && in.pdu_fresh;
  }
  static bool RtdRequest(const StateInputs& in) {
    return in.rtd_edge && in.bse_valid && in.brake_for_rtd && in.pedal_released;
  }
};

// One row: in `from` (or any non-terminal state when `any` is set), if
// `guard` holds, go to `to`. Rows are tried in order; the first match wins.
struct Transition {
  VcuState from;
  bool any;
  bool (*guard)(const StateInputs&);
  VcuState to;
  const char* reason;
};

// Table-driven VCU state machine:
//   INIT -> IDLE (LV on) -> PRECHARGE -> TS_ACTIVE -> RTD_WAIT -> DRIVE
//   DRIVE -> FAULT on any drive-exit condition; FAULT -> IDLE once clear
//   any -> BSPD_TRIPPED (terminal) when the hardware BSPD latch is set
class StateMachine {
 public:
  // Applies at most one transition. Returns true if the state changed.
  bool Step(const StateInputs& in);

  VcuState state() const { return state_; }
  const char* last_reason() const { return last_reason_; }

  static const Transition* table();
  static size_t table_size();

 private:
  VcuState state_ = VcuState::kInit;
  const char* last_reason_ = "boot";
};

}  // namespace vcu
