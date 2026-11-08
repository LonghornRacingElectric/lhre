#pragma once

#include "bms_chain.hpp"

namespace hvc::board {

// hvc::BmsChain over the vendored ADBMS6830 library (adbms6830/), which
// talks SPI2 with CS on PB13 (see adbms6830/src/mcuWrapper.c).
class AdbmsChain final : public BmsChain {
 public:
  size_t ReadSerialIds(BmbId* out, size_t max) override;
  void Configure() override;
  void ReadCellVoltages() override;
};

}  // namespace hvc::board
