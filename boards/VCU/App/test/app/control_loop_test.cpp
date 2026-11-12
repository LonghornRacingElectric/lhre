// The VCU control tick (app/control_loop.cpp): which fault bits each input
// raises, the contactor and mode checks, and the torque arbitration
// between the pedal path, regen and launch. The module tests cover each
// block; these pin how the tick wires them together.

#include <gtest/gtest.h>

#include <functional>
#include <memory>
#include <string>

#include "app/vcu_app.hpp"
#include "lhre_can_vcu.hpp"
#include "safety/faults.hpp"
#include "test/bench.hpp"

namespace {

using lhre::can::EventMode;
using VcuState = lhre::can::VcuStateMachine;
using vcu::bench::Bench;
namespace msg = lhre::can::vcu;

class ControlLoopTest : public ::testing::Test {
 protected:
  void SetUp() override { Rebuild(); }

  void Rebuild() {
    app_ = std::make_unique<vcu::App>(b_.peripherals(), &b_.params);
  }

  void ReachDrive() {
    b_.Energize();
    b_.SetBrakePsi(500.0f);
    b_.SetPedalTravel(0.0f);
    b_.Run(*app_, 200);
    ASSERT_EQ(app_->state(), VcuState::kRtdWait);
    b_.PressRtd(true);
    b_.Run(*app_, 50);
    ASSERT_EQ(app_->state(), VcuState::kDrive);
    b_.SetBrakePsi(0.0f);
    b_.Run(*app_, 50);
  }

  // Every fault bit raised at any tick over `ms`, so a bit that shows for
  // a single tick before the state machine reacts still counts.
  uint32_t FaultsSeen(uint32_t ms) {
    uint32_t seen = 0;
    for (uint32_t i = 0; i < ms; ++i) {
      b_.Run(*app_, 1);
      seen |= app_->faults();
    }
    return seen;
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

struct FaultCase {
  const char* name;
  std::function<void(Bench&)> apply;
  uint32_t expected;  // bits it adds over a healthy, energized car
};

// Each input raises its own bits and no others. Most of these are
// diagnostic only (no torque cut), so a missing bit shows nowhere else.
// An APPS out of its window is also an implausibility (T.4.2.10).
TEST_F(ControlLoopTest, EachInputRaisesItsOwnFaultBits) {
  const FaultCase cases[] = {
      {"apps1 disconnected", [](Bench& b) { b.SetPedals(0.1f, 0.5f); },
       vcu::kFaultApps1Range | vcu::kFaultAppsImplausible},
      {"apps1 over range", [](Bench& b) { b.SetPedals(4.9f, 0.5f); },
       vcu::kFaultApps1Range | vcu::kFaultAppsImplausible},
      {"apps2 disconnected", [](Bench& b) { b.SetPedals(0.5f, 0.1f); },
       vcu::kFaultApps2Range | vcu::kFaultAppsImplausible},
      {"apps2 over range", [](Bench& b) { b.SetPedals(0.5f, 4.9f); },
       vcu::kFaultApps2Range | vcu::kFaultAppsImplausible},
      {"bse1 disconnected", [](Bench& b) { b.SetBrakes(0.1f, 0.5f); },
       vcu::kFaultBse1Range},
      {"bse2 over range", [](Bench& b) { b.SetBrakes(0.5f, 4.9f); },
       vcu::kFaultBse2Range},
      {"steering disconnected", [](Bench& b) { b.SetSteering(0.1f); },
       vcu::kFaultSteeringRange},
      {"bspd latched", [](Bench& b) { b.latch_state.Write(true); },
       vcu::kFaultBspdTripped},
      {"dui silent", [](Bench& b) { b.dui_alive = false; },
       vcu::kFaultDuiTimeout},
      {"pdu silent", [](Bench& b) { b.pdu_alive = false; },
       vcu::kFaultPduTimeout | vcu::kFaultBse3Unavailable},
      {"motor disconnected", [](Bench& b) { b.motor_disconnect.Write(true); },
       vcu::kFaultMotorDisconnect},
      {"hard brake input without pressure",
       [](Bench& b) { b.hard_braking.Write(true); },
       vcu::kFaultHardBrakeMismatch},
      {"loop out live with loop in dead",
       [](Bench& b) { b.SetLoop(0.0f, 24.0f); },
       vcu::kFaultShutdownOpen | vcu::kFaultShutdownSenseImplausible},
      {"inverter silent", [](Bench& b) { b.inverter_alive = false; },
       vcu::kFaultInverterTimeout},
      {"hvc silent", [](Bench& b) { b.hvc_alive = false; },
       vcu::kFaultHvcTimeout},
  };
  for (const FaultCase& c : cases) {
    SCOPED_TRACE(c.name);
    Bench b;
    const auto app = std::make_unique<vcu::App>(b.peripherals());
    b.Energize();
    b.Run(*app, 300);
    const uint32_t healthy = app->faults();
    ASSERT_EQ(healthy, 0u);
    c.apply(b);
    b.Run(*app, 300);
    EXPECT_EQ(app->faults(), c.expected)
        << "got 0x" << std::hex << app->faults() << " want 0x" << c.expected;
  }
}

TEST_F(ControlLoopTest, BrakeOverThrottleIsReportedWithoutBeingATorqueCut) {
  ReachDrive();
  b_.SetPedalTravel(0.5f);
  b_.SetBrakePsi(500.0f);
  b_.Run(*app_, 100);
  EXPECT_EQ(app_->faults() & vcu::kFaultBrakeOverThrottle,
            vcu::kFaultBrakeOverThrottle);
  EXPECT_EQ(app_->faults() & vcu::kTorqueCutFaults, 0u);
  EXPECT_EQ(app_->state(), VcuState::kDrive);
  EXPECT_EQ(app_->torque_command(), 0.0f);
}

// A contactor that stays open on one side is not "closed".
TEST_F(ControlLoopTest, OneOpenContactorNeverReachesDrive) {
  b_.negative_contactor_open = true;
  b_.Energize();
  b_.SetBrakePsi(500.0f);
  b_.SetPedalTravel(0.0f);
  b_.Run(*app_, 200);
  b_.PressRtd(true);
  b_.Run(*app_, 200);
  EXPECT_NE(app_->state(), VcuState::kDrive);
  EXPECT_NE(app_->state(), VcuState::kRtdWait);
}

TEST_F(ControlLoopTest, AContactorOpeningInDriveIsReportedAndLeavesDrive) {
  ReachDrive();
  EXPECT_EQ(FaultsSeen(30) & vcu::kFaultContactorsOpen, 0u);
  b_.negative_contactor_open = true;
  EXPECT_NE(FaultsSeen(30) & vcu::kFaultContactorsOpen, 0u);
  EXPECT_NE(app_->state(), VcuState::kDrive);
  EXPECT_EQ(app_->torque_command(), 0.0f);
}

// Mode 0 is a real request (the default parameter set), not "none".
TEST_F(ControlLoopTest, ModeZeroIsAppliedFromTheShellAndFromCan) {
  b_.Run(*app_, 50);
  ASSERT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
  Shell("/mode 0");
  b_.Run(*app_, 10);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kUnassigned);

  Shell("/mode 4");
  b_.Run(*app_, 10);
  ASSERT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
  lhre::can::pi::VcuModeCommand m;
  m.event_mode = static_cast<uint8_t>(EventMode::kUnassigned);
  b_.car.Send(m.ToFrame());
  b_.Run(*app_, 10);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kUnassigned);
}

// The inverter fault clear waits from the first tick, not from clock zero,
// so a late start (the RTOS scheduler starting after init) still waits.
TEST_F(ControlLoopTest, FaultClearDelayCountsFromTheFirstTick) {
  b_.clock.Advance(60000);
  Rebuild();
  const uint32_t delay = b_.params.inverter.fault_clear_delay_ms;
  const auto clears = [&] {
    int n = 0;
    for (const lhal::CanFrame& f : b_.vcu_critical.sent()) {
      n += msg::InverterParameterRequest::Matches(f.id) ? 1 : 0;
    }
    return n;
  };
  b_.Run(*app_, delay - 10);
  EXPECT_EQ(clears(), 0);
  // And it does go out once the delay has passed, so the zero above isn't
  // just a clear that never fires.
  b_.Run(*app_, 20);
  EXPECT_EQ(clears(), 1);
}

// The torque path keeps its filter state while torque is allowed: a held
// pedal settles to one value instead of restarting every tick.
TEST_F(ControlLoopTest, HeldPedalSettlesToASteadyTorque) {
  ReachDrive();
  b_.SetPedalTravel(0.6f);
  b_.Run(*app_, 500);
  const float settled = app_->torque_command();
  ASSERT_GT(settled, 0.0f);
  b_.Run(*app_, 30);
  EXPECT_FLOAT_EQ(app_->torque_command(), settled);
}

// The RTD tone plays again on the next entry into DRIVE.
TEST_F(ControlLoopTest, RtdTonePlaysOnEveryEntryIntoDrive) {
  for (int entry = 0; entry < 2; ++entry) {
    SCOPED_TRACE(entry);
    ReachDrive();
    EXPECT_TRUE(b_.dac.playing());
    b_.PressRtd(false);
    b_.Run(*app_, 100);
    ASSERT_NE(app_->state(), VcuState::kDrive);
    EXPECT_FALSE(b_.dac.playing());
    b_.Run(*app_, b_.params.buzzer.duration_ms);
  }
}

// Spins the motor up 50 rpm per 5 ms frame, far past the limiter's
// acceleration threshold, holding `travel`.
void SpinUp(Bench& b, vcu::App& app, float travel) {
  b.SetPedalTravel(travel);
  for (int i = 0; i < 40; ++i) {
    b.motor_rpm = static_cast<int16_t>(b.motor_rpm + 50);
    b.Run(app, 5);
  }
}

// The legacy acceleration limiter trims torque when the motor spins up too
// fast. It needs the speed history the torque path keeps between ticks.
TEST_F(ControlLoopTest, AccelerationLimiterTrimsAFastSpinUp) {
  b_.params.traction.enabled = true;
  Rebuild();
  ReachDrive();
  SpinUp(b_, *app_, 0.6f);
  const auto s = app_->snapshot();
  ASSERT_GT(s.torque.power_limited, 0.0f);
  EXPECT_LT(s.torque.traction, s.torque.power_limited);
  EXPECT_LT(app_->torque_command(), s.torque.power_limited);
}

// Wheel-slip control replaces the acceleration limiter rather than
// stacking a second cut on top of it.
TEST_F(ControlLoopTest, WheelSlipControlTurnsTheAccelerationLimiterOff) {
  b_.params.traction.enabled = true;
  b_.params.wheel_control.enabled = true;
  Rebuild();
  for (int i = 0; i < 4; ++i) {
    b_.wheel_alive[i] = true;
    b_.wheel_rad_s[i] = 50;
  }
  ReachDrive();
  SpinUp(b_, *app_, 0.6f);
  const auto s = app_->snapshot();
  ASSERT_TRUE(s.wheel_control.valid);
  ASSERT_GT(s.torque.power_limited, 0.0f);
  EXPECT_FLOAT_EQ(s.torque.traction, s.torque.power_limited);
}

// A launch request is answered once. One refused outside DRIVE must not
// arm the launch later on its own.
TEST_F(ControlLoopTest, RefusedLaunchRequestDoesNotArmLater) {
  b_.params.event_mode = EventMode::kAcceleration;
  b_.params.launch.enabled = true;
  Rebuild();
  for (bool& alive : b_.wheel_alive) alive = true;
  b_.Energize();
  b_.SetBrakePsi(500.0f);
  b_.SetPedalTravel(0.0f);
  b_.Run(*app_, 200);
  ASSERT_EQ(app_->state(), VcuState::kRtdWait);
  Shell("/launch arm");
  b_.Run(*app_, 10);
  ASSERT_FALSE(app_->snapshot().launch_armed);
  b_.PressRtd(true);
  b_.Run(*app_, 100);  // DRIVE, braked and stationary: armable now
  ASSERT_EQ(app_->state(), VcuState::kDrive);
  EXPECT_FALSE(app_->snapshot().launch_armed);
}

// Launch arms only from a standstill. Motor rpm stays at zero here, so the
// front wheel speed is the only thing that can refuse.
TEST_F(ControlLoopTest, LaunchArmsOnlyBelowHalfAMetrePerSecond) {
  b_.params.event_mode = EventMode::kAcceleration;
  b_.params.launch.enabled = true;
  Rebuild();
  for (bool& alive : b_.wheel_alive) alive = true;
  ReachDrive();
  b_.SetBrakePsi(100.0f);

  for (float& w : b_.wheel_rad_s) w = 3.0f;  // 0.61 m/s
  b_.Run(*app_, 100);
  Shell("/launch arm");
  b_.Run(*app_, 6);
  EXPECT_FALSE(app_->snapshot().launch_armed);

  for (float& w : b_.wheel_rad_s) w = 2.0f;  // 0.41 m/s
  b_.Run(*app_, 100);
  Shell("/launch arm");
  b_.Run(*app_, 6);
  EXPECT_TRUE(app_->snapshot().launch_armed);
}

// A torque cut clears the acceleration limiter's trim, so torque does not
// come back already trimmed by a spin-up from before the cut.
TEST_F(ControlLoopTest, TorqueCutClearsTheAccelerationLimiterTrim) {
  b_.params.traction.enabled = true;
  Rebuild();
  ReachDrive();
  SpinUp(b_, *app_, 0.6f);
  auto s = app_->snapshot();
  ASSERT_LT(s.torque.traction, s.torque.power_limited);

  // Brake over throttle cuts torque without leaving DRIVE. The motor keeps
  // accelerating, so a limiter that still ran would hold its trim.
  b_.SetBrakePsi(500.0f);
  SpinUp(b_, *app_, 0.6f);
  ASSERT_EQ(app_->state(), VcuState::kDrive);
  ASSERT_EQ(app_->torque_command(), 0.0f);
  s = app_->snapshot();
  ASSERT_GT(s.torque.power_limited, 0.0f);
  EXPECT_FLOAT_EQ(s.torque.traction, s.torque.power_limited);
}

class RegenArbitrationTest : public ControlLoopTest {
 protected:
  void SetUp() override {
    b_.params.regen.enabled = true;
    b_.params.regen.max_current_a = 45;
    b_.params.regen.hard_cut_current_a = 54;
    b_.params.regen.max_pack_v = 546;
    b_.params.regen.pack_resistance_ohm = 0.442f;
    Rebuild();
  }
};

// With regen enabled, the pedal still drives the car until the rear brake
// is applied; then positive torque stops even when regen can't run.
TEST_F(RegenArbitrationTest, BrakePressureStopsPositiveTorque) {
  ReachDrive();  // no wheel speeds, so regen itself never engages
  b_.SetPedalTravel(0.6f);
  b_.Run(*app_, 300);
  EXPECT_GT(app_->torque_command(), 0.0f);

  b_.SetBrakePsi(100.0f);  // over engage_max_psi, under brake-over-throttle
  b_.Run(*app_, 300);
  ASSERT_EQ(app_->state(), VcuState::kDrive);
  EXPECT_EQ(app_->faults() & vcu::kFaultBrakeOverThrottle, 0u);
  EXPECT_EQ(app_->torque_command(), 0.0f);
}

// The hard-braking comparator input alone keeps regen off, even when the
// firmware's own pressure check doesn't see hard braking.
TEST_F(RegenArbitrationTest, HardBrakingInputAloneBlocksRegen) {
  for (int i = 0; i < 4; ++i) {
    b_.wheel_alive[i] = true;
    b_.wheel_rad_s[i] = 50;
  }
  ReachDrive();
  b_.motor_rpm = 1600;
  b_.linelock_out.Write(true);
  b_.Run(*app_, 220);
  b_.SetBrakePsi(100.0f);  // well under the firmware hard-brake threshold
  b_.Run(*app_, 300);
  ASSERT_FALSE(app_->snapshot().pedals.firmware_hard_brake);
  EXPECT_LT(app_->torque_command(), 0.0f);

  b_.hard_braking.Write(true);
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->torque_command(), 0.0f);
}

}  // namespace
