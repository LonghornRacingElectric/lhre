#pragma once

#include "lhal/stm32/hal.hpp"

#ifdef HAL_I2C_MODULE_ENABLED

#include "lhal/i2c.hpp"

namespace lhal::stm32 {

// Wraps an I2C_HandleTypeDef configured by board bring-up code.
class I2c final : public lhal::I2cMaster {
 public:
  explicit I2c(I2C_HandleTypeDef* hi2c) : hi2c_(hi2c) {}

  Status Write(uint16_t address, const uint8_t* data, size_t len,
               uint32_t timeout_ms) override {
    return ToStatus(HAL_I2C_Master_Transmit(
        hi2c_, static_cast<uint16_t>(address << 1),
        const_cast<uint8_t*>(data), static_cast<uint16_t>(len), timeout_ms));
  }

  Status Read(uint16_t address, uint8_t* data, size_t len,
              uint32_t timeout_ms) override {
    return ToStatus(HAL_I2C_Master_Receive(
        hi2c_, static_cast<uint16_t>(address << 1), data,
        static_cast<uint16_t>(len), timeout_ms));
  }

  Status WriteRead(uint16_t address, const uint8_t* write_data,
                   size_t write_len, uint8_t* read_data, size_t read_len,
                   uint32_t timeout_ms) override {
    Status s = Write(address, write_data, write_len, timeout_ms);
    if (!IsOk(s)) {
      return s;
    }
    return Read(address, read_data, read_len, timeout_ms);
  }

  // Escape hatch for anything LHAL doesn't cover.
  I2C_HandleTypeDef* handle() { return hi2c_; }

 private:
  I2C_HandleTypeDef* hi2c_;
};

}  // namespace lhal::stm32

#endif  // HAL_I2C_MODULE_ENABLED
