#include "asm330lhb.hpp"

#include <cmath>

namespace hvc {
namespace {

int16_t LittleEndian16(const uint8_t* bytes) {
  return static_cast<int16_t>(static_cast<uint16_t>(bytes[0]) |
                              (static_cast<uint16_t>(bytes[1]) << 8));
}

// Datasheet sensitivities: ±2 g -> 0.061 mg/LSB, ±250 dps -> 8.75 mdps/LSB,
// temperature 256 LSB/°C with 0 LSB = 25 °C.
int32_t AccelMg(int16_t raw) { return raw * 61 / 1000; }
int32_t GyroMdps(int16_t raw) { return raw * 875 / 100; }
int32_t TempCentiC(int16_t raw) { return 2500 + raw * 100 / 256; }

int32_t RadToDecideg(float rad) {
  return static_cast<int32_t>(std::lround(rad * (1800.0f / 3.14159265f)));
}

}  // namespace

Tilt TiltFromAccel(const ImuSample& s) {
  const float x = static_cast<float>(s.accel_mg[0]);
  const float y = static_cast<float>(s.accel_mg[1]);
  const float z = static_cast<float>(s.accel_mg[2]);
  Tilt t;
  t.roll_decideg = RadToDecideg(std::atan2(y, z));
  t.pitch_decideg = RadToDecideg(std::atan2(-x, std::sqrt(y * y + z * z)));
  return t;
}

bool Asm330lhb::ReadWhoAmI(uint8_t* id) {
  return bus_ != nullptr && bus_->Read(kRegWhoAmI, id, 1);
}

bool Asm330lhb::Init() {
  initialized_ = false;
  uint8_t id = 0;
  if (!ReadWhoAmI(&id) || id != kWhoAmIValue) {
    return false;
  }
  // CTRL3_C first: BDU and auto-increment must be on before burst reads.
  if (!bus_->Write(kRegCtrl3C, kCtrl3C) ||
      !bus_->Write(kRegCtrl1Xl, kCtrl1Xl) ||
      !bus_->Write(kRegCtrl2G, kCtrl2G)) {
    return false;
  }
  initialized_ = true;
  return true;
}

bool Asm330lhb::Read(ImuSample* out) {
  if (!initialized_ && !Init()) {
    return false;
  }
  // OUT_TEMP_L..OUTZ_H_A: temp (2), gyro x/y/z (6), accel x/y/z (6).
  uint8_t raw[14];
  if (!bus_->Read(kRegOutTempL, raw, sizeof(raw))) {
    initialized_ = false;  // re-check WHO_AM_I next time
    return false;
  }
  out->temp_centi_c = TempCentiC(LittleEndian16(&raw[0]));
  for (int axis = 0; axis < 3; ++axis) {
    out->gyro_mdps[axis] = GyroMdps(LittleEndian16(&raw[2 + 2 * axis]));
    out->accel_mg[axis] = AccelMg(LittleEndian16(&raw[8 + 2 * axis]));
  }
  return true;
}

}  // namespace hvc
