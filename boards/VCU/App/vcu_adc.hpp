#pragma once
#include "lhal/adc.hpp"
namespace vcu {
class AdcScan : public lhal::AdcScan {
 public:
  virtual uint32_t completed_scans() const = 0;
};
}  // namespace vcu
