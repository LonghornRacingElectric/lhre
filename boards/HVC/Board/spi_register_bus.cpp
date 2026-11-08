#include "spi_register_bus.hpp"

namespace hvc::board {

bool SpiRegisterBus::Read(uint8_t reg, uint8_t* data, size_t len) {
  uint8_t address = reg | 0x80;  // bit 7 set = read
  HAL_GPIO_WritePin(cs_port_, cs_pin_, GPIO_PIN_RESET);
  bool ok = HAL_SPI_Transmit(spi_, &address, 1, kTimeoutMs) == HAL_OK &&
            HAL_SPI_Receive(spi_, data, static_cast<uint16_t>(len),
                            kTimeoutMs) == HAL_OK;
  HAL_GPIO_WritePin(cs_port_, cs_pin_, GPIO_PIN_SET);
  return ok;
}

bool SpiRegisterBus::Write(uint8_t reg, uint8_t value) {
  uint8_t frame[2] = {static_cast<uint8_t>(reg & 0x7F), value};
  HAL_GPIO_WritePin(cs_port_, cs_pin_, GPIO_PIN_RESET);
  bool ok = HAL_SPI_Transmit(spi_, frame, sizeof(frame), kTimeoutMs) == HAL_OK;
  HAL_GPIO_WritePin(cs_port_, cs_pin_, GPIO_PIN_SET);
  return ok;
}

}  // namespace hvc::board
