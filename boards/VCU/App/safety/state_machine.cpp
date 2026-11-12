#include "safety/state_machine.hpp"

namespace vcu {
namespace {

using C = StateConditions;
using lhre::can::HvcState;

bool Always(const StateInputs&) { return true; }
bool BspdLatched(const StateInputs& in) { return in.bspd_latched; }
bool Precharging(const StateInputs& in) { return C::Precharging(in); }
bool Energized(const StateInputs& in) { return C::Energized(in); }
bool NotEnergized(const StateInputs& in) { return !C::Energized(in); }
bool PrechargeAborted(const StateInputs& in) {
  return !C::Precharging(in) && !C::Energized(in);
}
bool Ready(const StateInputs& in) { return C::ReadyForRtd(in); }
bool NotReady(const StateInputs& in) { return !C::ReadyForRtd(in); }
bool RtdRequest(const StateInputs& in) { return C::RtdRequest(in); }

// Check liveness and shutdown directly, independent of the fault mask.
bool DriveExitFault(const StateInputs& in) {
  return !C::Energized(in) || !in.loop_closed || in.torque_cut_fault ||
         !in.inverter_ready || !in.pdu_fresh;
}
bool RtdReleased(const StateInputs& in) {
  return in.rtd_release_exits_drive && !in.rtd_pressed;
}
bool FaultCleared(const StateInputs& in) { return !in.torque_cut_fault; }

constexpr Transition kTable[] = {
    // The BSPD row comes first and applies everywhere: torque goes to zero
    // the same tick, and nothing leaves BSPD_TRIPPED.
    {VcuState::kInit, true, BspdLatched, VcuState::kBspdTripped,
     "BSPD latch set"},
    {VcuState::kInit, false, Always, VcuState::kIdle, "boot complete"},
    {VcuState::kIdle, false, Energized, VcuState::kTsActive, "HVC energized"},
    {VcuState::kIdle, false, Precharging, VcuState::kPrecharge,
     "HVC precharging"},
    {VcuState::kPrecharge, false, Energized, VcuState::kTsActive,
     "precharge complete"},
    {VcuState::kPrecharge, false, PrechargeAborted, VcuState::kIdle,
     "precharge aborted or HVC silent"},
    {VcuState::kTsActive, false, NotEnergized, VcuState::kIdle,
     "HVC not energized"},
    {VcuState::kTsActive, false, Ready, VcuState::kRtdWait,
     "drive preconditions met"},
    {VcuState::kRtdWait, false, NotEnergized, VcuState::kIdle,
     "HVC not energized"},
    {VcuState::kRtdWait, false, NotReady, VcuState::kTsActive,
     "drive preconditions lost"},
    {VcuState::kRtdWait, false, RtdRequest, VcuState::kDrive, "ready to drive"},
    {VcuState::kDrive, false, DriveExitFault, VcuState::kFault,
     "drive-exit fault"},
    {VcuState::kDrive, false, RtdReleased, VcuState::kTsActive,
     "RTD switch released"},
    {VcuState::kFault, false, FaultCleared, VcuState::kIdle, "faults cleared"},
};

}  // namespace

const Transition* StateMachine::table() { return kTable; }
size_t StateMachine::table_size() { return sizeof(kTable) / sizeof(kTable[0]); }

bool StateMachine::Step(const StateInputs& in) {
  if (state_ == VcuState::kBspdTripped) {
    return false;  // terminal: only a power cycle clears the hardware latch
  }
  for (const Transition& t : kTable) {
    if ((t.any || t.from == state_) && t.guard(in)) {
      if (t.to == state_) {
        return false;
      }
      state_ = t.to;
      last_reason_ = t.reason;
      return true;
    }
  }
  return false;
}

}  // namespace vcu
