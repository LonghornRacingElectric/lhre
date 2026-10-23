#pragma once

#include "vcu_config.hpp"

namespace vcu {

struct SteeringOutputs {
  float travel = 0.0f;  // fraction of the 0.5-4.5 V span, unclamped
  float angle_deg = 0.0f;
  bool range_fault = false;  // diagnostic only
};

// lhre-2026 steering conversion (app_freertos.c:230-242, 341) on the Rev A
// span.
SteeringOutputs ComputeSteering(float sensor_v, const SteeringParams& p);

// Front share of total brake pressure, held while the total is below
// `min_total_psi` (lhre-2026 vcu_can.c:331-341). Starts at 0.
class BrakeBias {
 public:
  float Update(float front_psi, float rear_psi, float min_total_psi) {
    const float total = front_psi + rear_psi;
    if (total > min_total_psi) {
      bias_ = front_psi / total;
    }
    return bias_;
  }
  float value() const { return bias_; }

 private:
  float bias_ = 0.0f;
};

}  // namespace vcu
