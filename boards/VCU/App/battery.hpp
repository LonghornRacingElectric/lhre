#pragma once

#include <cstdint>

#include "longhorn/debounce.hpp"
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

// Update OCV only after the pack rests; hold the estimate under load.
class BatteryEstimator {
 public:
  BatteryOutputs Update(const BatteryInputs& in, const BatteryParams& p);
  const BatteryOutputs& outputs() const { return out_; }

 private:
  uint32_t rest_ms_ = 0;
  longhorn::Ema ocv_min_;
  longhorn::Ema ocv_max_;
  BatteryOutputs out_;
};

}  // namespace vcu
