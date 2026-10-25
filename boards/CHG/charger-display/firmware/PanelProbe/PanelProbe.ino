// Bench-only standard Arduino SPI diagnostic. No UART2 or charger commands.
#include <Arduino.h>
#include <SPI.h>
SPIClass panelSpi(HSPI);
void command(uint8_t c, const uint8_t* data = nullptr, size_t size = 0) {
  panelSpi.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWrite(15, LOW);
  digitalWrite(2, LOW);
  panelSpi.transfer(c);
  digitalWrite(2, HIGH);
  for (size_t i = 0; i < size; i++) panelSpi.transfer(data[i]);
  digitalWrite(15, HIGH);
  panelSpi.endTransaction();
}
void reg(uint8_t c, std::initializer_list<uint8_t> data) {
  command(c, data.begin(), data.size());
}
void initPanel() {
  command(0x01);
  delay(150);
  command(0x11);
  delay(120);
  reg(0x36, {0x48});
  reg(0x3A, {0x55});
  reg(0xF0, {0xC3});
  reg(0xF0, {0x96});
  reg(0xB4, {0x01});
  reg(0xB7, {0xC6});
  reg(0xC0, {0x80, 0x45});
  reg(0xC1, {0x13});
  reg(0xC2, {0xA7});
  reg(0xC5, {0x20});
  reg(0xE8, {0x40, 0x8A, 0, 0, 0x29, 0x19, 0xA5, 0x33});
  reg(0xE0, {0xD0, 8, 15, 6, 6, 0x33, 0x30, 0x33, 0x47, 0x17, 0x13, 0x13, 0x2B,
             0x31});
  reg(0xE1, {0xD0, 10, 0x11, 11, 9, 7, 0x2F, 0x33, 0x47, 0x38, 0x15, 0x16, 0x2C,
             0x32});
  reg(0xF0, {0x3C});
  reg(0xF0, {0x69});
  delay(120);
  command(0x29);
  delay(30);
}
void fill(uint16_t color) {
  reg(0x2A, {0, 0, 1, 0x3F});
  reg(0x2B, {0, 0, 1, 0xDF});
  panelSpi.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWrite(15, LOW);
  digitalWrite(2, LOW);
  panelSpi.transfer(0x2C);
  digitalWrite(2, HIGH);
  uint8_t buffer[512];
  for (int i = 0; i < 512; i += 2) {
    buffer[i] = color >> 8;
    buffer[i + 1] = color;
  }
  for (int i = 0; i < 600; i++) panelSpi.writeBytes(buffer, sizeof(buffer));
  digitalWrite(15, HIGH);
  panelSpi.endTransaction();
}
void readId() {
  panelSpi.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  digitalWrite(15, LOW);
  digitalWrite(2, LOW);
  panelSpi.transfer(0xD3);
  digitalWrite(2, HIGH);
  uint8_t a = panelSpi.transfer(0), b = panelSpi.transfer(0),
          c = panelSpi.transfer(0), d = panelSpi.transfer(0);
  digitalWrite(15, HIGH);
  panelSpi.endTransaction();
  Serial.printf("RAW_LCD_ID %02x %02x %02x %02x\n", a, b, c, d);
}
void setup() {
  Serial.begin(115200);
  for (int p : {15, 2, 27, 33, 5, 4, 22, 16, 17}) {
    pinMode(p, OUTPUT);
    digitalWrite(p, HIGH);
  }
  panelSpi.begin(14, 12, 13, 15);
  initPanel();
}
void loop() {
  static uint8_t phase = 0;
  const uint16_t colors[] = {0xF800, 0x07E0, 0x001F, 0xFFFF, 0};
  fill(colors[phase]);
  readId();
  Serial.printf("PANEL_PROBE phase=%u color=%04x\n", phase, colors[phase]);
  phase = (phase + 1) % 5;
  delay(2000);
}
