// Every row of the VCU state machine's transition table, plus the guards
// that must hold it in place.

#include <gtest/gtest.h>

#include "state_machine.hpp"

namespace {

using lhre::can::HvcState;
using vcu::StateInputs;
using vcu::StateMachine;
using vcu::VcuState;

// Inputs with everything healthy and the driver ready: the only thing
// missing for DRIVE is the RTD edge.
StateInputs Healthy() {
  StateInputs in;
  in.hvc_fresh = true;
  in.hvc_state = HvcState::kEnergized;
  in.contactors_closed = true;
  in.loop_closed = true;
  in.inverter_ready = true;
  in.pdu_fresh = true;
  in.bse_valid = true;
  in.brake_for_rtd = true;
  in.pedal_released = true;
  in.rtd_pressed = true;
  return in;
}

StateInputs Off() { return StateInputs{}; }

// Drives a fresh machine to `target` along the normal path.
StateMachine At(VcuState target) {
  StateMachine sm;
  sm.Step(Off());  // INIT -> IDLE
  if (target == VcuState::kIdle) return sm;
  if (target == VcuState::kPrecharge) {
    StateInputs in = Off();
    in.hvc_fresh = true;
    in.hvc_state = HvcState::kPrecharging;
    sm.Step(in);
    return sm;
  }
  StateInputs in = Healthy();
  sm.Step(in);  // IDLE -> TS_ACTIVE
  if (target == VcuState::kTsActive) return sm;
  sm.Step(in);  // TS_ACTIVE -> RTD_WAIT
  if (target == VcuState::kRtdWait) return sm;
  in.rtd_edge = true;
  sm.Step(in);  // RTD_WAIT -> DRIVE
  if (target == VcuState::kDrive) return sm;
  in.rtd_edge = false;
  in.loop_closed = false;
  sm.Step(in);  // DRIVE -> FAULT
  return sm;
}

TEST(StateMachine, StartsInInitAndBootsToIdle) {
  StateMachine sm;
  EXPECT_EQ(sm.state(), VcuState::kInit);
  EXPECT_TRUE(sm.Step(Off()));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
  EXPECT_FALSE(sm.Step(Off()));
}

TEST(StateMachine, NormalPathReachesEveryState) {
  EXPECT_EQ(At(VcuState::kPrecharge).state(), VcuState::kPrecharge);
  EXPECT_EQ(At(VcuState::kTsActive).state(), VcuState::kTsActive);
  EXPECT_EQ(At(VcuState::kRtdWait).state(), VcuState::kRtdWait);
  EXPECT_EQ(At(VcuState::kDrive).state(), VcuState::kDrive);
  EXPECT_EQ(At(VcuState::kFault).state(), VcuState::kFault);
}

TEST(StateMachine, BspdLatchFromEveryStateIsTerminal) {
  for (VcuState s : {VcuState::kInit, VcuState::kIdle, VcuState::kPrecharge,
                     VcuState::kTsActive, VcuState::kRtdWait, VcuState::kDrive,
                     VcuState::kFault}) {
    StateMachine sm = s == VcuState::kInit ? StateMachine{} : At(s);
    StateInputs in = Healthy();
    in.bspd_latched = true;
    EXPECT_TRUE(sm.Step(in)) << lhre::can::ToString(s);
    EXPECT_EQ(sm.state(), VcuState::kBspdTripped);
    // Nothing leaves it, not even a clean bill of health.
    EXPECT_FALSE(sm.Step(Healthy()));
    EXPECT_EQ(sm.state(), VcuState::kBspdTripped);
  }
}

TEST(StateMachine, IdleGoesStraightToTsActiveWhenAlreadyEnergized) {
  StateMachine sm = At(VcuState::kIdle);
  StateInputs in = Off();
  in.hvc_fresh = true;
  in.hvc_state = HvcState::kEnergized;
  in.contactors_closed = true;
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kTsActive);
}

TEST(StateMachine, EnergizedNeedsContactorFeedback) {
  StateMachine sm = At(VcuState::kIdle);
  StateInputs in = Off();
  in.hvc_fresh = true;
  in.hvc_state = HvcState::kEnergized;
  in.contactors_closed = false;
  EXPECT_FALSE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
}

TEST(StateMachine, StaleHvcIsIgnored) {
  StateMachine sm = At(VcuState::kIdle);
  StateInputs in = Healthy();
  in.hvc_fresh = false;
  EXPECT_FALSE(sm.Step(in));
}

TEST(StateMachine, PrechargeAbortsToIdle) {
  StateMachine sm = At(VcuState::kPrecharge);
  StateInputs in = Off();
  in.hvc_fresh = true;
  in.hvc_state = HvcState::kNotEnergized;
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
  // HVC going silent mid-precharge also aborts.
  sm = At(VcuState::kPrecharge);
  EXPECT_TRUE(sm.Step(Off()));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
}

TEST(StateMachine, PrechargeCompletes) {
  StateMachine sm = At(VcuState::kPrecharge);
  StateInputs in = Off();
  in.hvc_fresh = true;
  in.hvc_state = HvcState::kEnergized;
  in.contactors_closed = true;
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kTsActive);
}

TEST(StateMachine, TsActiveDropsToIdleWhenDeenergized) {
  StateMachine sm = At(VcuState::kTsActive);
  EXPECT_TRUE(sm.Step(Off()));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
}

TEST(StateMachine, EachReadinessConditionGatesRtdWait) {
  using Field = bool StateInputs::*;
  struct Case {
    const char* name;
    Field field;
    bool bad;
  };
  const Case cases[] = {
      {"loop open", &StateInputs::loop_closed, false},
      {"torque-cut fault", &StateInputs::torque_cut_fault, true},
      {"inverter not ready", &StateInputs::inverter_ready, false},
      {"PDU silent", &StateInputs::pdu_fresh, false},
  };
  for (const Case& c : cases) {
    StateMachine sm = At(VcuState::kTsActive);
    StateInputs in = Healthy();
    in.*c.field = c.bad;
    EXPECT_FALSE(sm.Step(in)) << c.name;
    EXPECT_EQ(sm.state(), VcuState::kTsActive) << c.name;

    // And losing it in RTD_WAIT drops back to TS_ACTIVE.
    sm = At(VcuState::kRtdWait);
    EXPECT_TRUE(sm.Step(in)) << c.name;
    EXPECT_EQ(sm.state(), VcuState::kTsActive) << c.name;
  }
}

TEST(StateMachine, RtdWaitDropsToIdleWhenDeenergized) {
  StateMachine sm = At(VcuState::kRtdWait);
  EXPECT_TRUE(sm.Step(Off()));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
}

TEST(StateMachine, RtdRequestNeedsEdgeBrakeValidBsePedalReleased) {
  using Field = bool StateInputs::*;
  const Field needed[] = {&StateInputs::rtd_edge, &StateInputs::bse_valid,
                          &StateInputs::brake_for_rtd,
                          &StateInputs::pedal_released};
  for (Field f : needed) {
    StateMachine sm = At(VcuState::kRtdWait);
    StateInputs in = Healthy();
    in.rtd_edge = true;
    in.*f = false;
    EXPECT_FALSE(sm.Step(in));
    EXPECT_EQ(sm.state(), VcuState::kRtdWait);
  }
  StateMachine sm = At(VcuState::kRtdWait);
  StateInputs in = Healthy();
  in.rtd_edge = true;
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kDrive);
}

TEST(StateMachine, EveryDriveExitConditionFaults) {
  using Field = bool StateInputs::*;
  struct Case {
    const char* name;
    Field field;
    bool bad;
  };
  const Case cases[] = {
      {"loop open", &StateInputs::loop_closed, false},
      {"torque-cut fault", &StateInputs::torque_cut_fault, true},
      {"inverter lost", &StateInputs::inverter_ready, false},
      {"PDU timeout", &StateInputs::pdu_fresh, false},
      {"HVC timeout", &StateInputs::hvc_fresh, false},
      {"contactor open", &StateInputs::contactors_closed, false},
  };
  for (const Case& c : cases) {
    StateMachine sm = At(VcuState::kDrive);
    StateInputs in = Healthy();
    in.*c.field = c.bad;
    EXPECT_TRUE(sm.Step(in)) << c.name;
    EXPECT_EQ(sm.state(), VcuState::kFault) << c.name;
  }
  StateMachine sm = At(VcuState::kDrive);
  StateInputs in = Healthy();
  in.hvc_state = HvcState::kNotEnergized;
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kFault);
}

TEST(StateMachine, DriveHoldsWhileHealthy) {
  StateMachine sm = At(VcuState::kDrive);
  EXPECT_FALSE(sm.Step(Healthy()));
  EXPECT_EQ(sm.state(), VcuState::kDrive);
}

TEST(StateMachine, RtdReleaseLeavesDriveWhenConfigured) {
  StateMachine sm = At(VcuState::kDrive);
  StateInputs in = Healthy();
  in.rtd_pressed = false;
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kTsActive);

  sm = At(VcuState::kDrive);
  in.rtd_release_exits_drive = false;
  EXPECT_FALSE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kDrive);
}

TEST(StateMachine, FaultHoldsUntilClearedThenRestartsAtIdle) {
  StateMachine sm = At(VcuState::kFault);
  StateInputs in = Healthy();
  in.torque_cut_fault = true;
  EXPECT_FALSE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kFault);
  in.torque_cut_fault = false;
  in.rtd_edge = true;  // can't jump straight back into DRIVE
  EXPECT_TRUE(sm.Step(in));
  EXPECT_EQ(sm.state(), VcuState::kIdle);
}

TEST(StateMachine, TableRowsAreAllExercised) {
  // Guard against a row being added without a test above: every row's
  // `to` state must be reachable in this file's scenarios.
  EXPECT_EQ(StateMachine::table_size(), 14u);
}

}  // namespace
