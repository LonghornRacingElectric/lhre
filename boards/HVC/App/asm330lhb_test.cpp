// Host tests for the ASM330LHB driver against an in-memory register file.

#include "asm330lhb.hpp"

#include <gtest/gtest.h>

#include <cstring>

namespace {

// A fake chip: 128 registers with auto-increment reads.
class FakeBus final : public hvc::RegisterBus {
 public:
  bool Read(uint8_t reg, uint8_t* data, size_t len) override {
    if (fail || reg + len > sizeof(regs)) {
      return false;
    }
    std::memcpy(data, &regs[reg], len);
    return true;
  }
  bool Write(uint8_t reg, uint8_t value) override {
    if (fail) {
      return false;
    }
    regs[reg] = value;
    return true;
  }

  void Set16(uint8_t reg, int16_t value) {
    regs[reg] = static_cast<uint8_t>(value & 0xFF);
    regs[reg + 1] = static_cast<uint8_t>((value >> 8) & 0xFF);
  }

  uint8_t regs[128] = {};
  bool fail = false;
};

TEST(Asm330lhb, InitConfiguresChipWhenWhoAmIMatches) {
  FakeBus bus;
  bus.regs[hvc::Asm330lhb::kRegWhoAmI] = hvc::Asm330lhb::kWhoAmIValue;
  hvc::Asm330lhb imu(&bus);
  ASSERT_TRUE(imu.Init());
  EXPECT_TRUE(imu.initialized());
  EXPECT_EQ(bus.regs[hvc::Asm330lhb::kRegCtrl3C], hvc::Asm330lhb::kCtrl3C);
  EXPECT_EQ(bus.regs[hvc::Asm330lhb::kRegCtrl1Xl], hvc::Asm330lhb::kCtrl1Xl);
  EXPECT_EQ(bus.regs[hvc::Asm330lhb::kRegCtrl2G], hvc::Asm330lhb::kCtrl2G);
}

TEST(Asm330lhb, InitRejectsWrongChip) {
  FakeBus bus;
  bus.regs[hvc::Asm330lhb::kRegWhoAmI] = 0xFF;  // floating MISO
  hvc::Asm330lhb imu(&bus);
  EXPECT_FALSE(imu.Init());
  EXPECT_EQ(bus.regs[hvc::Asm330lhb::kRegCtrl1Xl], 0);  // nothing written
}

TEST(Asm330lhb, ReadScalesRawCounts) {
  FakeBus bus;
  bus.regs[hvc::Asm330lhb::kRegWhoAmI] = hvc::Asm330lhb::kWhoAmIValue;
  bus.Set16(0x20, 256);     // temp: +1 °C over 25
  bus.Set16(0x22, 1000);    // gyro x: 8750 mdps
  bus.Set16(0x24, -1000);   // gyro y
  bus.Set16(0x26, 0);       // gyro z
  bus.Set16(0x28, 16393);   // accel x: ~1 g at 0.061 mg/LSB
  bus.Set16(0x2A, -16393);  // accel y
  bus.Set16(0x2C, 0);       // accel z
  hvc::Asm330lhb imu(&bus);

  hvc::ImuSample s;
  ASSERT_TRUE(imu.Read(&s));  // initializes on first use
  EXPECT_EQ(s.temp_centi_c, 2600);
  EXPECT_EQ(s.gyro_mdps[0], 8750);
  EXPECT_EQ(s.gyro_mdps[1], -8750);
  EXPECT_EQ(s.gyro_mdps[2], 0);
  EXPECT_EQ(s.accel_mg[0], 999);
  EXPECT_EQ(s.accel_mg[1], -999);
  EXPECT_EQ(s.accel_mg[2], 0);
}

TEST(Asm330lhb, TiltFromGravity) {
  hvc::ImuSample s;
  s.accel_mg[2] = 1000;  // flat
  hvc::Tilt t = hvc::TiltFromAccel(s);
  EXPECT_EQ(t.roll_decideg, 0);
  EXPECT_EQ(t.pitch_decideg, 0);

  s.accel_mg[1] = 1000;  // rolled 90° onto its side
  s.accel_mg[2] = 0;
  t = hvc::TiltFromAccel(s);
  EXPECT_EQ(t.roll_decideg, 900);

  s.accel_mg[0] = -55;  // the bench reading: slight tilt
  s.accel_mg[1] = 0;
  s.accel_mg[2] = 1017;
  t = hvc::TiltFromAccel(s);
  EXPECT_EQ(t.pitch_decideg, 31);  // 3.1°
}

TEST(Asm330lhb, BusErrorDropsInitSoItIsRetried) {
  FakeBus bus;
  bus.regs[hvc::Asm330lhb::kRegWhoAmI] = hvc::Asm330lhb::kWhoAmIValue;
  hvc::Asm330lhb imu(&bus);
  ASSERT_TRUE(imu.Init());
  bus.fail = true;
  hvc::ImuSample s;
  EXPECT_FALSE(imu.Read(&s));
  EXPECT_FALSE(imu.initialized());
  bus.fail = false;
  EXPECT_TRUE(imu.Read(&s));
}

}  // namespace
