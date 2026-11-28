#pragma once
#include <Arduino.h>
#include <SPI.h>
// Standard Arduino SPI transport. TFT_eSPI is used only for drawing into RAM.
class PanelHAL {
  SPIClass spi_{HSPI};
  void reg(uint8_t c, std::initializer_list<uint8_t> data) {
    spi_.beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0));
    digitalWrite(15, LOW);
    digitalWrite(2, LOW);
    spi_.transfer(c);
    digitalWrite(2, HIGH);
    for (uint8_t value : data) spi_.transfer(value);
    digitalWrite(15, HIGH);
    spi_.endTransaction();
  }
  uint16_t adc(uint8_t c) {
    spi_.transfer(c);
    uint16_t result = uint16_t(spi_.transfer(0)) << 8;
    return (result | spi_.transfer(0)) >> 3;
  }

 public:
  void begin() {
    for (int p : {15, 2, 27, 33, 5}) {
      pinMode(p, OUTPUT);
      digitalWrite(p, HIGH);
    }
    pinMode(36, INPUT);
    spi_.begin(14, 12, 13, -1);
    reg(0x01, {});
    delay(150);
    reg(0x11, {});
    delay(120);
    reg(0x36, {0x28});
    reg(0x3A, {0x55});
    reg(0xF0, {0xC3});
    reg(0xF0, {0x96});
    reg(0xB4, {1});
    reg(0xB7, {0xC6});
    reg(0xC0, {0x80, 0x45});
    reg(0xC1, {0x13});
    reg(0xC2, {0xA7});
    reg(0xC5, {0x20});
    reg(0xE8, {0x40, 0x8A, 0, 0, 0x29, 0x19, 0xA5, 0x33});
    reg(0xE0, {0xD0, 8, 15, 6, 6, 0x33, 0x30, 0x33, 0x47, 0x17, 0x13, 0x13,
               0x2B, 0x31});
    reg(0xE1, {0xD0, 10, 0x11, 11, 9, 7, 0x2F, 0x33, 0x47, 0x38, 0x15, 0x16,
               0x2C, 0x32});
    reg(0xF0, {0x3C});
    reg(0xF0, {0x69});
    delay(120);
    reg(0x29, {});
    delay(30);
  }
  void push(int y, uint8_t* bytes) {
    // Sprite memory already holds RGB565 in wire byte order.
    reg(0x2A, {0, 0, 1, 0xDF});
    uint16_t bottom = y + 79;
    reg(0x2B,
        {uint8_t(y >> 8), uint8_t(y), uint8_t(bottom >> 8), uint8_t(bottom)});
    spi_.beginTransaction(SPISettings(10000000, MSBFIRST, SPI_MODE0));
    digitalWrite(15, LOW);
    digitalWrite(2, LOW);
    spi_.transfer(0x2C);
    digitalWrite(2, HIGH);
    // Use bounded chunks and keep CS asserted over the complete 480x80 tile.
    for (size_t offset = 0; offset < 480u * 80u * 2u; offset += 512)
      spi_.writeBytes(bytes + offset,
                      min(size_t(512), 480u * 80u * 2u - offset));
    digitalWrite(15, HIGH);
    spi_.endTransaction();
  }
  bool readRaw(uint16_t& x, uint16_t& y, uint16_t threshold = 350) {
    if (digitalRead(36) != LOW) return false;
    spi_.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
    digitalWrite(15, HIGH);
    digitalWrite(33, LOW);
    uint16_t z1 = adc(0xB0), z2 = adc(0xC0);
    bool pressed = (z1 > 0 && 4095 + int(z1) - int(z2) > threshold);
    if (pressed) {
      uint16_t xs[5], ys[5];
      for (int i = 0; i < 5; i++) {
        xs[i] = adc(0xD0);
        ys[i] = adc(0x90);
      }
      for (int i = 0; i < 5; i++)
        for (int j = i + 1; j < 5; j++) {
          if (xs[j] < xs[i]) {
            uint16_t v = xs[i];
            xs[i] = xs[j];
            xs[j] = v;
          }
          if (ys[j] < ys[i]) {
            uint16_t v = ys[i];
            ys[i] = ys[j];
            ys[j] = v;
          }
        }
      x = xs[2];
      y = ys[2];
      pressed = xs[4] - xs[0] < 100 && ys[4] - ys[0] < 100 && x > 0 &&
                x < 4095 && y > 0 && y < 4095;
    }
    adc(0x90);  // PD=00 powers down between samples and re-enables PENIRQ.
    digitalWrite(33, HIGH);
    spi_.endTransaction();
    return pressed;
  }
};
