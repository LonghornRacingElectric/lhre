// Deterministic tests of the VCU vehicle app through the scheduler-less
// Step() path, against LHAL host fakes (see vcu_test_bench.hpp).

#include <gtest/gtest.h>

#include <memory>
#include <string>

#include "faults.hpp"
#include "lhre_can_vcu.hpp"
#include "vcu_app.hpp"
#include "vcu_test_bench.hpp"

namespace {

using lhre::can::EventMode;
using lhre::can::HvcState;
using lhre::can::VcuState;
using vcu::bench::Bench;
namespace msg = lhre::can::vcu;

class VcuAppTest : public ::testing::Test {
 protected:
  void SetUp() override { app_ = std::make_unique<vcu::App>(b_.peripherals()); }

  // Energized, everything healthy, brake on, pedal released: RTD_WAIT.
  void ReachRtdWait() {
    b_.Energize();
    b_.SetBrakePsi(500.0f);
    b_.SetPedalTravel(0.0f);
    b_.Run(*app_, 200);
    ASSERT_EQ(app_->state(), VcuState::kRtdWait);
  }

  void ReachDrive() {
    ReachRtdWait();
    b_.PressRtd(true);
    b_.Run(*app_, 50);
    ASSERT_EQ(app_->state(), VcuState::kDrive);
    b_.SetBrakePsi(0.0f);
    b_.Run(*app_, 50);
  }

  std::string Shell(const std::string& line) {
    b_.vcp.TakeTx();
    const std::string in = line + "\r";
    b_.vcp.InjectRx(reinterpret_cast<const uint8_t*>(in.data()), in.size());
    app_->Step();
    const auto tx = b_.vcp.TakeTx();
    return std::string(tx.begin(), tx.end());
  }

  Bench b_;
  std::unique_ptr<vcu::App> app_;
};

TEST_F(VcuAppTest, BootsIntoSafeIdle) {
  // Before any tick: outputs already safe (constructor).
  EXPECT_FALSE(b_.linelock_ctrl.Read());
  EXPECT_FALSE(b_.amp_shutdown.Read());
  EXPECT_TRUE(b_.bms_led.Read());
  EXPECT_TRUE(b_.imd_led.Read());
  EXPECT_FALSE(b_.crit_stb.Read());
  EXPECT_FALSE(b_.data_stb.Read());

  b_.Run(*app_, 30);
  EXPECT_EQ(app_->state(), VcuState::kIdle);
  EXPECT_EQ(app_->torque_command(), 0.0f);

  msg::InverterTorqueCommand cmd;
  ASSERT_TRUE(b_.LastFrame(b_.car, &cmd));
  EXPECT_FALSE(cmd.enable);
  EXPECT_EQ(cmd.torque_request_raw, 0);
  EXPECT_TRUE(cmd.direction);
  EXPECT_NEAR(cmd.torque_limit(), 230.0f, 0.1f);
}

TEST_F(VcuAppTest, PrechargeThenTractiveActive) {
  b_.hvc_state = HvcState::kPrecharging;
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->state(), VcuState::kPrecharge);
  b_.Energize();
  b_.Run(*app_, 30);
  // Brake not pressed and pedal released is fine for RTD_WAIT; only the
  // RTD press needs the brake.
  EXPECT_EQ(app_->state(), VcuState::kRtdWait);
}

TEST_F(VcuAppTest, FullDriveSequenceProducesTorqueAndSound) {
  ReachRtdWait();
  b_.PressRtd(true);
  b_.Run(*app_, 40);
  ASSERT_EQ(app_->state(), VcuState::kDrive);

  // RTD sound: amp on and the DAC playing a 1 kHz sine.
  EXPECT_TRUE(b_.amp_shutdown.Read());
  EXPECT_TRUE(b_.dac.playing());
  EXPECT_EQ(b_.dac.sample_rate_hz(), 32000u);

  // Release the brake before the pedal: pressing both trips
  // brake-over-throttle (see BrakeOverThrottleCutsUntilPedalReleased).
  b_.SetBrakePsi(0.0f);
  b_.Run(*app_, 100);
  b_.SetPedalTravel(0.6f);
  b_.Run(*app_, 200);
  EXPECT_GT(app_->torque_command(), 0.0f);
  msg::InverterTorqueCommand cmd;
  ASSERT_TRUE(b_.LastFrame(b_.car, &cmd));
  EXPECT_TRUE(cmd.enable);
  EXPECT_GT(cmd.torque_request(), 0.0f);

  // Sound stops by the configured duration (1200 ms).
  b_.Run(*app_, 1000);
  EXPECT_FALSE(b_.amp_shutdown.Read());
  EXPECT_FALSE(b_.dac.playing());
}

TEST_F(VcuAppTest, RtdHeldThroughBootNeverDrives) {
  b_.PressRtd(true);  // held from power-on
  b_.Energize();
  b_.SetBrakePsi(500.0f);
  b_.Run(*app_, 500);
  EXPECT_EQ(app_->state(), VcuState::kRtdWait);
  // Release and press again: now it counts.
  b_.PressRtd(false);
  b_.Run(*app_, 50);
  b_.PressRtd(true);
  b_.Run(*app_, 50);
  EXPECT_EQ(app_->state(), VcuState::kDrive);
}

TEST_F(VcuAppTest, RtdNeedsBrakeAndReleasedPedal) {
  b_.Energize();
  b_.SetBrakePsi(0.0f);
  b_.Run(*app_, 200);
  ASSERT_EQ(app_->state(), VcuState::kRtdWait);
  b_.PressRtd(true);
  b_.Run(*app_, 50);
  EXPECT_EQ(app_->state(), VcuState::kRtdWait) << "no brake";
  b_.PressRtd(false);
  b_.SetBrakePsi(500.0f);
  b_.SetPedalTravel(0.3f);
  b_.Run(*app_, 100);
  b_.PressRtd(true);
  b_.Run(*app_, 50);
  EXPECT_EQ(app_->state(), VcuState::kRtdWait) << "pedal pressed";
}

TEST_F(VcuAppTest, AppsImplausibilityZeroesTorqueAfter100ms) {
  ReachDrive();
  b_.SetPedalTravel(0.5f);
  b_.Run(*app_, 200);
  ASSERT_GT(app_->torque_command(), 0.0f);
  // 20 points of deviation.
  const auto& a = b_.params.apps;
  b_.SetPedals(a.apps1_v_at_0 + 0.6f * (a.apps1_v_at_100 - a.apps1_v_at_0),
               a.apps2_v_at_0 + 0.4f * (a.apps2_v_at_100 - a.apps2_v_at_0));
  b_.Run(*app_, 60);
  EXPECT_GT(app_->torque_command(), 0.0f) << "still inside 100 ms";
  b_.Run(*app_, 60);
  EXPECT_EQ(app_->torque_command(), 0.0f);
  EXPECT_NE(app_->faults() & vcu::kFaultAppsImplausible, 0u);
  EXPECT_EQ(app_->state(), VcuState::kFault);
}

TEST_F(VcuAppTest, BrakeOverThrottleCutsUntilPedalReleased) {
  ReachDrive();
  b_.SetPedalTravel(0.5f);
  b_.Run(*app_, 200);
  ASSERT_GT(app_->torque_command(), 0.0f);
  b_.SetBrakePsi(800.0f);
  b_.Run(*app_, 200);
  EXPECT_EQ(app_->torque_command(), 0.0f);
  EXPECT_EQ(app_->state(), VcuState::kDrive);
  // Brake released but pedal still above 5%: stays cut.
  b_.SetBrakePsi(0.0f);
  b_.Run(*app_, 200);
  EXPECT_EQ(app_->torque_command(), 0.0f);
  b_.SetPedalTravel(0.0f);
  b_.Run(*app_, 200);
  b_.SetPedalTravel(0.5f);
  b_.Run(*app_, 200);
  EXPECT_GT(app_->torque_command(), 0.0f);
}

TEST_F(VcuAppTest, BspdLatchIsTerminal) {
  ReachDrive();
  b_.SetPedalTravel(0.5f);
  b_.Run(*app_, 100);
  b_.latch_state.Write(true);
  b_.Run(*app_, 3);
  EXPECT_EQ(app_->state(), VcuState::kBspdTripped);
  EXPECT_EQ(app_->torque_command(), 0.0f);
  // Clearing the input (it can't really clear without a power cycle) does
  // not bring the state back.
  b_.latch_state.Write(false);
  b_.Run(*app_, 500);
  EXPECT_EQ(app_->state(), VcuState::kBspdTripped);
  msg::VcuFaults f;
  ASSERT_TRUE(b_.LastFrame(b_.car, &f));
  EXPECT_EQ(f.state, VcuState::kBspdTripped);
}

TEST_F(VcuAppTest, ShutdownOpenLeavesDriveAndCutsSound) {
  ReachRtdWait();
  b_.PressRtd(true);
  b_.Run(*app_, 40);
  ASSERT_TRUE(b_.amp_shutdown.Read());
  b_.SetLoop(24.0f, 0.0f);  // BSPD relay open
  b_.Run(*app_, 20);
  EXPECT_EQ(app_->state(), VcuState::kFault);
  EXPECT_FALSE(b_.amp_shutdown.Read());
  EXPECT_EQ(app_->snapshot().loop,
            lhre::can::ShutdownLoopState::kBspdRelayOpen);
}

TEST_F(VcuAppTest, CanTimeoutsLeaveDrive) {
  for (int which = 0; which < 3; ++which) {
    Bench b;
    auto app = std::make_unique<vcu::App>(b.peripherals());
    b.Energize();
    b.SetBrakePsi(500.0f);
    b.Run(*app, 200);
    b.PressRtd(true);
    b.Run(*app, 50);
    ASSERT_EQ(app->state(), VcuState::kDrive) << which;
    if (which == 0) b.hvc_alive = false;
    if (which == 1) b.inverter_alive = false;
    if (which == 2) b.pdu_alive = false;
    b.Run(*app, 250);
    EXPECT_EQ(app->state(), VcuState::kFault) << which;
    EXPECT_EQ(app->torque_command(), 0.0f) << which;
  }
}

TEST_F(VcuAppTest, InverterFaultBlocksRtd) {
  b_.inverter_run_faults = 0x10;
  b_.Energize();
  b_.Run(*app_, 200);
  EXPECT_EQ(app_->state(), VcuState::kTsActive);
  EXPECT_NE(app_->faults() & vcu::kFaultInverterFault, 0u);
}

TEST_F(VcuAppTest, RtdReleaseReturnsToTractiveActive) {
  ReachDrive();
  b_.PressRtd(false);
  b_.Run(*app_, 50);
  EXPECT_EQ(app_->state(), VcuState::kRtdWait);  // via TS_ACTIVE
}

TEST_F(VcuAppTest, BrakeFlagLatchSurvivesPowerCycleUntilDashReset) {
  b_.b1_disconnect.Write(true);
  b_.Run(*app_, 30);
  b_.b1_disconnect.Write(false);
  b_.Run(*app_, 30);
  EXPECT_NE(app_->faults() & vcu::kFaultBrakeFlagLatched, 0u);
  EXPECT_EQ(b_.board.stored, vcu::BrakeFlagLatch::kPersistedSet);

  // "Power cycle": a new app restores the latch from storage.
  app_ = std::make_unique<vcu::App>(b_.peripherals());
  b_.Run(*app_, 30);
  EXPECT_NE(app_->faults() & vcu::kFaultBrakeFlagLatched, 0u);

  // Dash reset clears it (edge), and the cleared state is persisted.
  b_.fault_reset = true;
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->faults() & vcu::kFaultBrakeFlagLatched, 0u);
  EXPECT_EQ(b_.board.stored, 0u);
}

TEST_F(VcuAppTest, DashResetIgnoredWhileFlagActive) {
  b_.b2_overvoltage.Write(true);
  b_.Run(*app_, 30);
  b_.fault_reset = true;
  b_.Run(*app_, 30);
  EXPECT_NE(app_->faults() & vcu::kFaultBrakeFlagLatched, 0u);
  // The reset has to be a fresh press after the flag clears.
  b_.b2_overvoltage.Write(false);
  b_.Run(*app_, 30);
  EXPECT_NE(app_->faults() & vcu::kFaultBrakeFlagLatched, 0u);
  b_.fault_reset = false;
  b_.Run(*app_, 30);
  b_.fault_reset = true;
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->faults() & vcu::kFaultBrakeFlagLatched, 0u);
}

TEST_F(VcuAppTest, ErrorLedsOnlyAddIndication) {
  b_.Run(*app_, 50);
  EXPECT_FALSE(b_.bms_led.Read());  // fresh, no fault
  EXPECT_FALSE(b_.imd_led.Read());
  b_.imd_error = true;
  b_.Run(*app_, 20);
  EXPECT_TRUE(b_.imd_led.Read());
  b_.imd_error = false;  // HVC un-latching doesn't turn it off
  b_.Run(*app_, 50);
  EXPECT_TRUE(b_.imd_led.Read());
  EXPECT_FALSE(b_.bms_led.Read());
  b_.indicators_alive = false;  // silence lights both
  b_.Run(*app_, 300);
  EXPECT_TRUE(b_.bms_led.Read());
}

TEST_F(VcuAppTest, ModeCommandAppliesOnlyOutsideDriveAndEchoes) {
  ReachDrive();
  lhre::can::pi::VcuModeCommand m;
  m.event_mode = EventMode::kSkidpad;
  b_.car.Send(m.ToFrame());
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
  b_.PressRtd(false);  // leave DRIVE
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kSkidpad);
  msg::VcuDriveState ds;
  ASSERT_TRUE(b_.LastFrame(b_.car, &ds));
  EXPECT_EQ(ds.event_mode, EventMode::kSkidpad);
}

TEST_F(VcuAppTest, LineLockRequestStaysZeroAndActualIsReported) {
  ReachDrive();
  EXPECT_FALSE(b_.linelock_ctrl.Read());
  b_.linelock_out.Write(true);  // readback from the hardware gate
  b_.Run(*app_, 10);
  msg::VcuDriveState ds;
  ASSERT_TRUE(b_.LastFrame(b_.car, &ds));
  EXPECT_TRUE(ds.line_lock_enabled);
  EXPECT_FALSE(ds.line_lock_request);
  EXPECT_EQ(ds.prndl_state, 1);
}

TEST_F(VcuAppTest, WatchdogStartedAndFedWhileHealthy) {
  b_.Run(*app_, 200);
  EXPECT_TRUE(b_.watchdog.started());
  EXPECT_EQ(b_.watchdog.timeout_ms(), vcu::App::kWatchdogTimeoutMs);
  EXPECT_GE(b_.watchdog.refresh_count(), 9u);
}

TEST_F(VcuAppTest, HardBrakeMismatchIsDiagnosticOnly) {
  ReachDrive();
  b_.hard_braking.Write(true);  // comparators say hard braking, BSE doesn't
  b_.Run(*app_, 100);
  EXPECT_NE(app_->faults() & vcu::kFaultHardBrakeMismatch, 0u);
  EXPECT_EQ(app_->state(), VcuState::kDrive);
}

TEST_F(VcuAppTest, DataBusCarriesDiagnostics) {
  b_.Run(*app_, 120);
  msg::VcuAdcCountsA counts;
  ASSERT_TRUE(b_.LastFrame(b_.logger, &counts));
  EXPECT_GT(counts.apps1, 0);
  // Fresh traffic for the next checks.
  b_.Run(*app_, 120);
  msg::VcuDiag diag;
  ASSERT_TRUE(b_.LastFrame(b_.logger, &diag));
  EXPECT_EQ(diag.reset_cause, lhre::can::ResetCause::kPowerOn);
}

TEST_F(VcuAppTest, ShellStateAndBootloaderGate) {
  b_.Run(*app_, 30);
  EXPECT_NE(Shell("/state").find("state=IDLE"), std::string::npos);
  EXPECT_NE(Shell("update.").find("entering bootloader"), std::string::npos);
  EXPECT_EQ(b_.board.bootloader_requests, 1);

  ReachDrive();
  EXPECT_NE(Shell("update.").find("refused"), std::string::npos);
  EXPECT_EQ(b_.board.bootloader_requests, 1);
}

}  // namespace
