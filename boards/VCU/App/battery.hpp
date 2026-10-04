#pragma once

#include <cstdint>

#include "debounce.hpp"
#include "vcu_config.hpp"

namespace vcu {

struct BatteryInputs {
  float dc_bus_current = 0.0f;  // inverter, negative = regen
  float dc_bus_voltage = 0.0f;
  float min_cell_v = 0.0f;  // HVC; <= 0 when not reported
  float max_cell_v = 0.0f;
  uint32_t dt_ms = 0;
};

struct BatteryOutputs {
  float ocv_min_cell_v = 0.0f;
  float ocv_max_cell_v = 0.0f;
  float soe_pct = 0.0f;
  double net_energy_wh = 0.0;
  double regen_energy_wh = 0.0;
};

// State of energy and energy metering, ported from lhre-2026 Battery.c.
// The open-circuit cell voltage is tracked only after the pack has rested
// (|I| below rest_current_a for rest_time_ms); until then the last estimate
// holds (0 at boot).
class BatteryEstimator {
 public:
  BatteryOutputs Update(const BatteryInputs& in, const BatteryParams& p);
  const BatteryOutputs& outputs() const { return out_; }

 private:
  uint32_t rest_ms_ = 0;
  Ema ocv_min_;
  Ema ocv_max_;
  BatteryOutputs out_;
};

}  // namespace vcu
