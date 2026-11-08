#pragma once

#include <cstddef>
#include <cstdint>

namespace hvc {

// Register-level access to one peripheral chip (e.g. the IMU over SPI4).
// LHAL has no SPI interface yet, so the app defines this narrow one: the
// board implements it with ST HAL (Board/spi_register_bus.*) and tests
// with an in-memory register file. Each call is a whole chip-select
// transaction.
class RegisterBus {
 public:
  virtual ~RegisterBus() = default;

  // Reads `len` consecutive registers starting at `reg` (the chip must
  // auto-increment). False on a bus error or timeout.
  virtual bool Read(uint8_t reg, uint8_t* data, size_t len) = 0;
  virtual bool Write(uint8_t reg, uint8_t value) = 0;
};

}  // namespace hvc
