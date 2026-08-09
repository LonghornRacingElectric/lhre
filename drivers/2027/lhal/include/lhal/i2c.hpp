#pragma once

#include <cstddef>
#include <cstdint>

#include "lhal/status.hpp"

namespace lhal {

class I2cMaster {
 public:
  virtual ~I2cMaster() = default;

  // `address` is the 7-bit device address (unshifted).
  virtual Status Write(uint16_t address, const uint8_t* data, size_t len,
                       uint32_t timeout_ms) = 0;
  virtual Status Read(uint16_t address, uint8_t* data, size_t len,
                      uint32_t timeout_ms) = 0;

  // Write then read on the same device — the typical register access pattern.
  virtual Status WriteRead(uint16_t address, const uint8_t* write_data,
                           size_t write_len, uint8_t* read_data,
                           size_t read_len, uint32_t timeout_ms) = 0;
};

}  // namespace lhal
