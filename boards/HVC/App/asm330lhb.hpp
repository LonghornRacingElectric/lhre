#pragma once

#include <cstdint>

#include "register_bus.hpp"

namespace hvc {

// One IMU reading, already scaled. Integer units keep printf free of the
// float formatter: milli-g, milli-degrees-per-second, hundredths of a °C.
struct ImuSample {
  int32_t accel_mg[3] = {0, 0, 0};       // x, y, z
  int32_t gyro_mdps[3] = {0, 0, 0};      // x, y, z
  int32_t temp_centi_c = 0;
};

// Board tilt from the accelerometer's view of gravity, in tenths of a
// degree. Only meaningful while the board isn't accelerating; unlike an
// integrated gyro angle it never drifts, so it suits bench checks.
//   roll:  rotation about x (y axis tipping toward z), 0 when flat
//   pitch: rotation about y (x axis tipping toward z), 0 when flat
struct Tilt {
  int32_t roll_decideg = 0;
  int32_t pitch_decideg = 0;
};
Tilt TiltFromAccel(const ImuSample& s);

// ST ASM330LHB 6-axis IMU (sprung IMU on SPI4, HVC-022). Runs it at a
// fixed bench configuration: 104 Hz, ±2 g, ±250 dps, block data update
// on so a sample's high and low bytes always belong together.
//
// Register addresses and scale factors are from the ASM330LHB datasheet
// (register map and mechanical characteristics tables).
class Asm330lhb {
 public:
  static constexpr uint8_t kRegWhoAmI = 0x0F;
  static constexpr uint8_t kRegCtrl1Xl = 0x10;  // accel ODR + full scale
  static constexpr uint8_t kRegCtrl2G = 0x11;   // gyro ODR + full scale
  static constexpr uint8_t kRegCtrl3C = 0x12;   // BDU, IF_INC, SW_RESET
  static constexpr uint8_t kRegOutTempL = 0x20;  // temp, then gyro, accel
  static constexpr uint8_t kWhoAmIValue = 0x6B;

  // CTRL1_XL: ODR 104 Hz (0100 in [7:4]), FS ±2 g (00 in [3:2]).
  static constexpr uint8_t kCtrl1Xl = 0x40;
  // CTRL2_G: ODR 104 Hz (0100 in [7:4]), FS ±250 dps (000 in [3:1]).
  static constexpr uint8_t kCtrl2G = 0x40;
  // CTRL3_C: BDU (bit 6) + IF_INC (bit 2, register auto-increment).
  static constexpr uint8_t kCtrl3C = 0x44;

  explicit Asm330lhb(RegisterBus* bus) : bus_(bus) {}

  // Checks WHO_AM_I and writes the configuration. False (and stays
  // uninitialized) if the chip doesn't answer with kWhoAmIValue.
  bool Init();
  bool initialized() const { return initialized_; }

  // Reads WHO_AM_I into `id`. False on a bus error.
  bool ReadWhoAmI(uint8_t* id);

  // Reads temperature, gyro and accel in one burst. Initializes first if
  // needed, so a chip that was slow to boot is picked up later.
  bool Read(ImuSample* out);

 private:
  RegisterBus* bus_;
  bool initialized_ = false;
};

}  // namespace hvc
