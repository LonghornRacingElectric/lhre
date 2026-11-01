// Closed-loop runs of the whole VCU app against a longitudinal car: the
// commanded torque accelerates the car, and the inverter frames report the
// resulting motor speed and DC power back to the app.

#include <gtest/gtest.h>

#include <algorithm>
#include <cmath>
#include <memory>

#include "app/vcu_app.hpp"
#include "test/bench.hpp"

namespace {

using VcuState = lhre::can::VcuStateMachine;
using vcu::bench::Bench;

constexpr float kPi = 3.14159265358979f;

// vehicle/vehicle.yml: sum of all mass_kg entries, rear wheel radius_m,
// pDriveline.finalDriveRatio and pMotor.rotorJ. No drag, rolling resistance
// or tire slip: this checks the controller, not lap performance.
constexpr float kMassKg = 243.742f;
constexpr float kWheelRadiusM = 0.2045f;
constexpr float kFinalDrive = 3.31f;
constexpr float kRotorJ = 0.02521f;
constexpr float kDcBusV = 500.0f;

// The logged spread of inverter V x I / (torque x omega), from 12f5a34.
constexpr float kRatioP50 = 0.858f;
constexpr float kRatioP99 = 0.990f;

struct Car {
  float speed_m_s = 0.0f;
  float ratio = kRatioP50;

  float MotorOmega() const { return speed_m_s / kWheelRadiusM * kFinalDrive; }
  float DcPowerW(float torque_nm) const {
    return ratio * torque_nm * MotorOmega();
  }
  void Step(float torque_nm, float dt_s) {
    const float reflected_mass = kMassKg + kRotorJ * kFinalDrive * kFinalDrive /
                                               (kWheelRadiusM * kWheelRadiusM);
    const float force = std::max(torque_nm, 0.0f) * kFinalDrive / kWheelRadiusM;
    speed_m_s += force / reflected_mass * dt_s;
  }
};

struct Trace {
  float peak_power_w = 0.0f;
  float final_power_w = 0.0f;
  float max_torque_drop_nm = 0.0f;
  float min_torque_after_launch_nm = 1e9f;
  bool cap_ever_limited = false;
};

class ClosedLoopTest : public ::testing::Test {
 protected:
  void Start() {
    app_ = std::make_unique<vcu::App>(b_.peripherals(), &b_.params);
    b_.Energize();
    b_.SetBrakePsi(500.0f);
    b_.SetPedalTravel(0.0f);
    b_.Run(*app_, 200);
    b_.PressRtd(true);
    b_.Run(*app_, 50);
    ASSERT_EQ(app_->state(), VcuState::kDrive);
    b_.SetBrakePsi(0.0f);
    b_.Run(*app_, 50);
  }

  // Full pedal from rest for `ms`, one app step and one car step per ms.
  Trace FullPedal(uint32_t ms) {
    Trace t;
    b_.SetPedalTravel(1.0f);
    float previous_nm = 0.0f;
    for (uint32_t i = 0; i < ms; ++i) {
      const float torque_nm = app_->torque_command();
      car_.Step(torque_nm, 0.001f);
      b_.motor_rpm = static_cast<int16_t>(car_.MotorOmega() * 30.0f / kPi);
      b_.dc_bus_voltage = kDcBusV;
      b_.dc_bus_current = car_.DcPowerW(torque_nm) / kDcBusV;
      b_.Run(*app_, 1);

      EXPECT_EQ(app_->state(), VcuState::kDrive);
      const auto s = app_->snapshot();
      t.cap_ever_limited |= s.torque.power_limited < s.torque.lookup;
      t.peak_power_w = std::max(t.peak_power_w, car_.DcPowerW(torque_nm));
      t.final_power_w = car_.DcPowerW(torque_nm);
      if (i > 200) {
        t.max_torque_drop_nm =
            std::max(t.max_torque_drop_nm, previous_nm - torque_nm);
        t.min_torque_after_launch_nm =
            std::min(t.min_torque_after_launch_nm, torque_nm);
      }
      previous_nm = torque_nm;
    }
    return t;
  }

  Bench b_;
  Car car_;
  std::unique_ptr<vcu::App> app_;
};

TEST_F(ClosedLoopTest, StockMapStaysUnderTheLimitWithoutTheCap) {
  car_.ratio = kRatioP99;
  Start();
  const Trace t = FullPedal(3000);  // stays under the map's 6000 rpm row
  EXPECT_FALSE(t.cap_ever_limited);
  EXPECT_LT(t.peak_power_w, b_.params.power_limit.limit_w);
  EXPECT_GT(car_.speed_m_s, 20.0f);
}

TEST_F(ClosedLoopTest, CapHoldsALowerLimitWithoutTorqueCycling) {
  b_.params.power_limit.limit_w = 40000.0f;
  car_.ratio = 1.05f * b_.params.power_limit.dc_power_ratio;
  Start();
  const Trace t = FullPedal(4000);
  EXPECT_TRUE(t.cap_ever_limited);
  EXPECT_LT(t.peak_power_w, 40000.0f * 1.05f + 500.0f);
  EXPECT_NEAR(t.final_power_w, 40000.0f, 600.0f);
  EXPECT_GT(app_->snapshot().torque.power_trim_w, 0.0f);
  EXPECT_GT(t.min_torque_after_launch_nm, 30.0f);
  EXPECT_LT(t.max_torque_drop_nm, 5.0f);
}

TEST_F(ClosedLoopTest, MedianRatioStaysUnderTheLimitAndLeavesTheTrimAtZero) {
  b_.params.power_limit.limit_w = 40000.0f;
  car_.ratio = kRatioP50;
  Start();
  const Trace t = FullPedal(4000);
  EXPECT_TRUE(t.cap_ever_limited);
  EXPECT_LT(t.peak_power_w, 40000.0f);
  EXPECT_FLOAT_EQ(app_->snapshot().torque.power_trim_w, 0.0f);
}

}  // namespace
