#pragma once

#include <unordered_map>

#include "lhal/i2c.hpp"

namespace lhal::host {

// Implement this to fake a device on the host I2C bus.
class I2cDevice {
 public:
  virtual ~I2cDevice() = default;

  virtual Status OnWrite(const uint8_t* data, size_t len) = 0;
  virtual Status OnRead(uint8_t* data, size_t len) = 0;
};

// Host I2C bus: attach fake devices by 7-bit address. Transfers to an
// address with no device return kError (NACK).
class I2c final : public lhal::I2cMaster {
 public:
  void Attach(uint16_t address, I2cDevice* device) {
    devices_[address] = device;
  }

  Status Write(uint16_t address, const uint8_t* data, size_t len,
               uint32_t /*timeout_ms*/) override {
    I2cDevice* d = FindDevice(address);
    return d != nullptr ? d->OnWrite(data, len) : Status::kError;
  }

  Status Read(uint16_t address, uint8_t* data, size_t len,
              uint32_t /*timeout_ms*/) override {
    I2cDevice* d = FindDevice(address);
    return d != nullptr ? d->OnRead(data, len) : Status::kError;
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

 private:
  I2cDevice* FindDevice(uint16_t address) const {
    auto it = devices_.find(address);
    return it != devices_.end() ? it->second : nullptr;
  }

  std::unordered_map<uint16_t, I2cDevice*> devices_;
};

}  // namespace lhal::host
