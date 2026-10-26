#pragma once

namespace vcu {

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
