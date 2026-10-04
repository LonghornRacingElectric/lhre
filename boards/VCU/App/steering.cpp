#include "steering.hpp"

#include "lookup.hpp"

namespace vcu {

SteeringOutputs ComputeSteering(float sensor_v, const SteeringParams& p) {
  SteeringOutputs out;
  out.travel = Normalize(sensor_v, p.v_at_0, p.v_at_100);
  out.range_fault = sensor_v < p.window_low_v || sensor_v > p.window_high_v;
  float pct = Clamp(out.travel, 0.0f, 1.0f);
  if (pct > p.wrap_above) {
    pct -= 1.0f;
  }
  out.angle_deg =
      ((pct - p.pct_zero) / p.pct_span - 0.5f) * p.range_deg + p.offset_deg;
  return out;
}

}  // namespace vcu
