#include <gtest/gtest.h>

#include "config/params.hpp"
#include "control/battery.hpp"

namespace {

const vcu::Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

TEST(Battery, SoeTracksRestedCellVoltage) {
  vcu::BatteryEstimator est;
  vcu::BatteryInputs in;
  in.min_cell_v = 3.78f;  // 46% on the table
  in.max_cell_v = 3.80f;
  in.dt_ms = 3;
  vcu::BatteryOutputs out;
  for (int i = 0; i < 300; ++i) out = est.Update(in, P().battery);
  EXPECT_EQ(out.soe_pct, 0.0f);  // < 1 s rest
  for (int i = 0; i < 3000; ++i) out = est.Update(in, P().battery);
  EXPECT_NEAR(out.soe_pct, 46.0f, 1.0f);
  // Under load the estimate holds.
  in.dc_bus_current = 100.0f;
  in.min_cell_v = 3.5f;
  for (int i = 0; i < 1000; ++i) out = est.Update(in, P().battery);
  EXPECT_NEAR(out.soe_pct, 46.0f, 1.0f);
}

TEST(Battery, EnergyMetering) {
  vcu::BatteryEstimator est;
  vcu::BatteryInputs in;
  in.dc_bus_voltage = 500.0f;
  in.dc_bus_current = 100.0f;  // 50 kW
  in.dt_ms = 1000;
  vcu::BatteryOutputs out = est.Update(in, P().battery);
  EXPECT_NEAR(out.net_energy_wh, 50000.0 / 3600.0, 1e-6);
  in.dc_bus_current = -20.0f;  // regen 10 kW
  out = est.Update(in, P().battery);
  EXPECT_NEAR(out.regen_energy_wh, 10000.0 / 3600.0, 1e-6);
}

}  // namespace
