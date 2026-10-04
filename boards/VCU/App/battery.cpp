#include "battery.hpp"

#include <cmath>

#include "lookup.hpp"

namespace vcu {

BatteryOutputs BatteryEstimator::Update(const BatteryInputs& in,
                                        const BatteryParams& p) {
  // app_freertos.c:361-366 fallbacks before the HVC reports cells.
  const float min_cell =
      in.min_cell_v > 0.0f ? in.min_cell_v : p.missing_min_cell_v;
  const float max_cell = in.max_cell_v > 0.0f ? in.max_cell_v : min_cell;

  if (std::fabs(in.dc_bus_current) < p.rest_current_a) {
    rest_ms_ += in.dt_ms;
  } else {
    rest_ms_ = 0;
  }
  if (rest_ms_ >= p.rest_time_ms) {
    out_.ocv_min_cell_v = ocv_min_.Update(min_cell, p.cell_voltage_ema_alpha);
    out_.ocv_max_cell_v = ocv_max_.Update(max_cell, p.cell_voltage_ema_alpha);
  }
  out_.soe_pct =
      Lookup1D(p.soe_from_cell_voltage, kLookupPoints, p.soe_min_cell_v,
               p.soe_max_cell_v, out_.ocv_min_cell_v);

  // Battery.c:46-57 energy metering on inverter V x I.
  const double step_wh = static_cast<double>(in.dc_bus_current) *
                         static_cast<double>(in.dc_bus_voltage) *
                         static_cast<double>(in.dt_ms) / 3.6e6;
  if (step_wh < 0.0) {
    out_.regen_energy_wh -= step_wh;
  }
  out_.net_energy_wh += step_wh;
  if (out_.net_energy_wh < 0.0) {
    out_.net_energy_wh = 0.0;
  }
  return out_;
}

}  // namespace vcu
