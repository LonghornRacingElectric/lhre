#pragma once

#include "config/params.hpp"

namespace vcu {

struct SteeringOutputs {
  float travel = 0.0f;  // fraction of the 0.5-4.5 V span, unclamped
  float angle_deg = 0.0f;
  bool range_fault = false;  // diagnostic only
};

// lhre-2026 steering conversion (app_freertos.c:230-242, 341) on the Rev A
// span.
SteeringOutputs ComputeSteering(float sensor_v, const SteeringParams& p);

}  // namespace vcu
