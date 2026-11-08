#pragma once

#include "lhal/stm32/hal.hpp"
#include "register_bus.hpp"

namespace hvc::board {

// hvc::RegisterBus over an ST HAL SPI handle with a GPIO chip select, in
// the common "address byte, bit 7 = read" register protocol (ST IMUs).
// The SPI peripheral (mode, clock) and the CS pin's idle-high level come
// from CubeMX; this only frames transactions.
class SpiRegisterBus final : public RegisterBus {
 public:
  SpiRegisterBus(SPI_HandleTypeDef* spi, GPIO_TypeDef* cs_port,
                 uint16_t cs_pin)
      : spi_(spi), cs_port_(cs_port), cs_pin_(cs_pin) {}

  bool Read(uint8_t reg, uint8_t* data, size_t len) override;
  bool Write(uint8_t reg, uint8_t value) override;

 private:
  static constexpr uint32_t kTimeoutMs = 10;

  SPI_HandleTypeDef* spi_;
  GPIO_TypeDef* cs_port_;
  uint16_t cs_pin_;
};

}  // namespace hvc::board
