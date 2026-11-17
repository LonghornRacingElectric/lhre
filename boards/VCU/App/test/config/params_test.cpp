// Invariants every event's parameter set must hold. A typo in a table
// (a torque curve that rises, a pedal map past 1.0) or a rule limit
// loosened while tuning one event fails here, before it reaches the car.

#include <gtest/gtest.h>

#include <cstddef>
#include <string>

#include "config/params.hpp"

namespace {

using lhre::can::EventMode;

constexpr EventMode kModes[] = {
    EventMode::kUnassigned, EventMode::kAcceleration, EventMode::kSkidpad,
    EventMode::kAutocross,  EventMode::kEndurance,
};

TEST(Params, EachModeGetsItsOwnSet) {
  for (EventMode mode : kModes) {
    EXPECT_EQ(vcu::ParamsFor(mode).event_mode, mode);
  }
}

TEST(Params, UnknownModeFallsBackToDefaults) {
  const vcu::Params& unknown = vcu::ParamsFor(static_cast<EventMode>(200));
  EXPECT_EQ(&unknown, &vcu::ParamsFor(EventMode::kUnassigned));
}

class EveryMode : public testing::TestWithParam<EventMode> {
 protected:
  const vcu::Params& P() const { return vcu::ParamsFor(GetParam()); }
};

TEST_P(EveryMode, PedalMapRisesFromZeroToAtMostFull) {
  const vcu::TorqueParams& t = P().torque;
  EXPECT_EQ(t.pedal_map[0], 0.0f);
  for (size_t i = 1; i < vcu::kLookupPoints; ++i) {
    EXPECT_GE(t.pedal_map[i], t.pedal_map[i - 1]) << "point " << i;
  }
  EXPECT_LE(t.pedal_map[vcu::kLookupPoints - 1], 1.0f);
  EXPECT_GT(t.pedal_curve_exponent, 0.0f);
}

TEST_P(EveryMode, TorqueCurveFallsWithSpeedWithinInverterLimit) {
  const vcu::Params& p = P();
  for (size_t i = 0; i < vcu::kLookupPoints; ++i) {
    EXPECT_GT(p.torque.power_limit_torque[i], 0.0f) << "point " << i;
    EXPECT_LE(p.torque.power_limit_torque[i], p.inverter.torque_limit_nm)
        << "point " << i;
    if (i > 0) {
      EXPECT_LE(p.torque.power_limit_torque[i],
                p.torque.power_limit_torque[i - 1])
          << "point " << i;
    }
  }
  EXPECT_LE(p.regen.max_torque_nm, p.inverter.torque_limit_nm);
}

TEST_P(EveryMode, PowerCapStaysUnderTheRuleLimit) {
  const vcu::PowerLimitParams& pl = P().power_limit;
  EXPECT_GT(pl.limit_w, 0.0f);
  EXPECT_LE(pl.limit_w, 80000.0f);  // accumulator power rule
  // The learned trim only lowers the cap; it must never take it to zero.
  EXPECT_GE(pl.trim_max_w, 0.0f);
  EXPECT_LT(pl.trim_max_w, pl.limit_w);
  EXPECT_GT(pl.dc_power_ratio, 0.0f);
  EXPECT_LE(pl.dc_power_ratio, 1.0f);
  EXPECT_GT(pl.min_omega_rad_s, 0.0f);
}

TEST_P(EveryMode, PedalPlausibilityLimitsMeetTheRules) {
  const vcu::Params& p = P();
  // T.4.2.4, T.4.2.5: over 10% deviation for over 100 ms cuts torque.
  EXPECT_LE(p.apps.max_deviation, 0.10f);
  EXPECT_LE(p.apps.implausible_ms, 100u);
  // T.4.3.3: a BSE fault lasting over 100 ms cuts torque.
  EXPECT_LE(p.bse.range_fault_ms, 100u);
  // EV.4.7: brakes plus 25% travel cuts torque until under 5%.
  EXPECT_LE(p.bse.bot_pedal_on, 0.25f);
  EXPECT_LE(p.bse.bot_pedal_off, 0.05f);
  EXPECT_LT(p.bse.bot_pedal_off, p.bse.bot_pedal_on);
  EXPECT_LT(p.apps.min_travel_deadzone, p.apps.max_travel_deadzone);
  EXPECT_LT(p.bse.pressed_off_psi, p.bse.pressed_on_psi);
}

TEST_P(EveryMode, ReadyToDriveSoundMeetsTheRule) {
  const vcu::BuzzerParams& b = P().buzzer;
  // EV.10.7.2: 1 to 3 s.
  EXPECT_GE(b.duration_ms, 1000u);
  EXPECT_LE(b.duration_ms, b.max_on_ms);
  EXPECT_LE(b.max_on_ms, 3000u);
}

TEST_P(EveryMode, StateOfEnergyTableRunsFromEmptyToFull) {
  const vcu::BatteryParams& b = P().battery;
  EXPECT_EQ(b.soe_from_cell_voltage[0], 0.0f);
  EXPECT_EQ(b.soe_from_cell_voltage[vcu::kLookupPoints - 1], 100.0f);
  for (size_t i = 1; i < vcu::kLookupPoints; ++i) {
    EXPECT_GE(b.soe_from_cell_voltage[i], b.soe_from_cell_voltage[i - 1])
        << "point " << i;
  }
  EXPECT_LT(b.soe_min_cell_v, b.soe_max_cell_v);
}

INSTANTIATE_TEST_SUITE_P(Params, EveryMode, testing::ValuesIn(kModes),
                         [](const testing::TestParamInfo<EventMode>& info) {
                           return "mode" +
                                  std::to_string(static_cast<int>(info.param));
                         });

}  // namespace
