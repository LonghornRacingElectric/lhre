// Host unit tests for the HVC application logic, using LHAL host fakes.

#include <gtest/gtest.h>

#include <cstring>
#include <string>
#include <vector>

#include "lhal/host/gpio.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "hvc_app.hpp"

namespace {

TEST(HvcApp, AnimatesRainbow) {
  lhal::host::TestClock clock;
  lhal::host::Pwm red;
  lhal::host::Pwm green;
  lhal::host::Pwm blue;

  hvc::Peripherals p;
  p.clock = &clock;
  p.led_red = &red;
  p.led_green = &green;
  p.led_blue = &blue;
  hvc::App app(p);

  // Simulate half a second in 10 ms main-loop ticks.
  for (int i = 0; i < 50; ++i) {
    app.Step();
    clock.Advance(10);
  }
  app.Step();

  // 20 ms period over 0.5 s: updates at t=0, 20, ..., 500 — 26 steps,
  // 0.52 s of animation. The rainbow starts at red and reaches green after
  // a third of its cycle, so it's partway along the red -> green edge.
  EXPECT_EQ(app.led_updates(), 26u);
  const float phase = 0.52f * 3.0f / longhorn::RgbLed::kRainbowCycleS;
  ASSERT_LT(phase, 1.0f);  // still on the red -> green edge
  const float cap = 0.5f;  // RgbLed's rainbow brightness cap
  EXPECT_NEAR(red.duty(), (1.0f - phase) * cap, 0.01f);
  EXPECT_NEAR(green.duty(), phase * cap, 0.01f);
  EXPECT_FLOAT_EQ(blue.duty(), 0.0f);
}

TEST(HvcApp, RunsWithoutOptionalPeripherals) {
  lhal::host::TestClock clock;
  hvc::Peripherals p;
  p.clock = &clock;
  hvc::App app(p);

  for (int i = 0; i < 100; ++i) {
    app.Step();
    clock.Advance(10);
  }
  EXPECT_EQ(app.led_updates(), 0u);
}

// A fake ASM330LHB: a register file that answers WHO_AM_I.
class FakeImuBus final : public hvc::RegisterBus {
 public:
  FakeImuBus() { regs[hvc::Asm330lhb::kRegWhoAmI] = hvc::Asm330lhb::kWhoAmIValue; }
  bool Read(uint8_t reg, uint8_t* data, size_t len) override {
    for (size_t i = 0; i < len; ++i) {
      data[i] = regs[(reg + i) & 0x7F];
    }
    return true;
  }
  bool Write(uint8_t reg, uint8_t value) override {
    regs[reg & 0x7F] = value;
    return true;
  }
  uint8_t regs[128] = {};
};

// A fake BMB chain: answers RDSID with whatever `ids` holds.
class FakeBmsChain final : public hvc::BmsChain {
 public:
  size_t ReadSerialIds(hvc::BmbId* out, size_t max) override {
    ++reads;
    const size_t n = max < hvc::kNumBmbs ? max : hvc::kNumBmbs;
    for (size_t i = 0; i < n; ++i) {
      out[i] = ids[i];
    }
    return n;
  }
  void Configure() override { ++configures; }
  void ReadCellVoltages() override { ++cell_reads; }
  hvc::BmbId ids[hvc::kNumBmbs];
  int reads = 0;
  int configures = 0;
  int cell_reads = 0;
};

// The bench pins and console, wired the way Board/main.cpp does.
struct Bench {
  lhal::host::TestClock clock;
  lhal::host::Uart console;
  lhal::host::Gpio leg2, leg3, leg4, leg5, leg15;
  lhal::host::Gpio air_minus, air_plus_closed, air_minus_closed;
  lhal::host::Gpio bms_error, imd_error, lamp_test;
  FakeImuBus imu;
  FakeBmsChain bms;

  hvc::Peripherals Peripherals() {
    hvc::Peripherals p;
    p.clock = &clock;
    p.console = &console;
    p.leg2 = &leg2;
    p.leg3 = &leg3;
    p.leg4 = &leg4;
    p.leg5 = &leg5;
    p.leg15 = &leg15;
    p.air_minus = &air_minus;
    p.air_plus_closed = &air_plus_closed;
    p.air_minus_closed = &air_minus_closed;
    p.bms_error = &bms_error;
    p.imd_error = &imd_error;
    p.lamp_test = &lamp_test;
    p.imu = &imu;
    p.bms = &bms;
    return p;
  }

  // Types a command line and returns everything the shell printed back.
  std::string Run(hvc::App& app, const std::string& line) {
    console.TakeTx();
    const std::string input = line + "\r";
    console.InjectRx(reinterpret_cast<const uint8_t*>(input.data()),
                     input.size());
    app.Step();
    const std::vector<uint8_t> tx = console.TakeTx();
    return std::string(tx.begin(), tx.end());
  }
};

TEST(HvcBench, BootsWithAirOpenAndImuConfigured) {
  Bench b;
  b.air_minus.Write(true);
  hvc::App app(b.Peripherals());
  EXPECT_FALSE(b.air_minus.Read());
  EXPECT_EQ(b.imu.regs[hvc::Asm330lhb::kRegCtrl1Xl], hvc::Asm330lhb::kCtrl1Xl);
}

TEST(HvcBench, LampTestOnForThreeSecondsAtBoot) {
  Bench b;
  hvc::App app(b.Peripherals());
  EXPECT_TRUE(b.lamp_test.Read());
  b.clock.Advance(hvc::App::kLampTestMs - 10);
  app.Step();
  EXPECT_TRUE(b.lamp_test.Read());
  b.clock.Advance(20);
  app.Step();
  EXPECT_FALSE(b.lamp_test.Read());
  // Stays off from then on.
  b.clock.Advance(10000);
  app.Step();
  EXPECT_FALSE(b.lamp_test.Read());
}

TEST(HvcBench, AirMinusClosesFiveSecondsAfterBoot) {
  Bench b;
  hvc::App app(b.Peripherals());
  EXPECT_FALSE(b.air_minus.Read());  // open at boot
  if (hvc::App::kAirMinusCloseMs > 0) {
    b.clock.Advance(hvc::App::kAirMinusCloseMs - 10);
    app.Step();
    EXPECT_FALSE(b.air_minus.Read());
    b.clock.Advance(20);
    app.Step();
    EXPECT_TRUE(b.air_minus.Read());
  }
}

TEST(HvcBench, AirCommandCancelsTimedClose) {
  Bench b;
  hvc::App app(b.Peripherals());
  b.Run(app, "/air off");
  b.clock.Advance(hvc::App::kAirMinusCloseMs + 100);
  app.Step();
  EXPECT_FALSE(b.air_minus.Read());
}

TEST(HvcBench, BmsErrorPulsesAtBoot) {
  Bench b;
  b.bms_error.Write(hvc::App::kBmsErrorPulseMs == 0);
  hvc::App app(b.Peripherals());
  EXPECT_EQ(b.bms_error.Read(), hvc::App::kBmsErrorPulseMs > 0);
  if (hvc::App::kBmsErrorPulseMs > 0) {
    b.clock.Advance(hvc::App::kBmsErrorPulseMs - 10);
    app.Step();
    EXPECT_TRUE(b.bms_error.Read());
  }
  b.clock.Advance(20);
  app.Step();
  EXPECT_FALSE(b.bms_error.Read());
}

TEST(HvcBench, LegsReportsWhereTheChainIsOpen) {
  Bench b;
  hvc::App app(b.Peripherals());
  b.leg2.Write(true);
  b.leg3.Write(true);
  b.leg4.Write(true);
  const std::string out = b.Run(app, "/legs");
  EXPECT_NE(out.find("leg2=1 leg3=1 leg4=1 leg5=0 leg15=0"),
            std::string::npos);
  EXPECT_NE(out.find("interlock"), std::string::npos);
}

TEST(HvcBench, AirDrivesAirMinusAndShowsFeedback) {
  Bench b;
  hvc::App app(b.Peripherals());
  b.air_plus_closed.Write(true);
  EXPECT_NE(b.Run(app, "/air on").find("AIR- cmd=1  AIR+ fb=1  AIR- fb=0"),
            std::string::npos);
  EXPECT_TRUE(b.air_minus.Read());
  b.Run(app, "/air off");
  EXPECT_FALSE(b.air_minus.Read());
}

TEST(HvcBench, BmsErrorFollowsCommands) {
  Bench b;
  hvc::App app(b.Peripherals());
  b.Run(app, "/bmserr on");
  EXPECT_TRUE(b.bms_error.Read());
  // Manual control sticks past the end of the boot pulse.
  b.clock.Advance(hvc::App::kBmsErrorPulseMs + 100);
  app.Step();
  EXPECT_TRUE(b.bms_error.Read());
  b.Run(app, "/bmserr off");
  EXPECT_FALSE(b.bms_error.Read());
}

TEST(HvcBench, ImuCommandReportsIdAndReading) {
  Bench b;
  b.imu.regs[0x28] = 0x09;  // accel x = 0x4009 = 16393 -> 999 mg
  b.imu.regs[0x29] = 0x40;
  hvc::App app(b.Peripherals());
  const std::string out = b.Run(app, "/imu");
  // All of gravity on +x: pitched -90°, not rotating.
  EXPECT_NE(out.find("ID=0x6B | roll=0.0 pitch=-90.0 deg | "
                     "gyro x=0.0 y=0.0 z=0.0 deg/s | "
                     "accel x=999 y=0 z=0 mg | T=25.00 C"),
            std::string::npos)
      << out;
}

TEST(HvcBench, ImuPrintsLowPassFilteredData) {
  Bench b;
  hvc::App app(b.Peripherals());
  // Accel z jumps between ~1100 and ~900 mg on alternate samples (noise
  // around 1 g); after a full filter window the printout shows the mean.
  auto set_accel_z = [&b](size_t i) {
    const int16_t raw = (i % 2 == 0) ? 18033 : 14755;  // 1100 / 900 mg
    b.imu.regs[0x2C] = static_cast<uint8_t>(raw & 0xFF);
    b.imu.regs[0x2D] = static_cast<uint8_t>((raw >> 8) & 0xFF);
  };
  size_t i = 0;
  for (; i < 2 * hvc::ImuFilter::kTaps; ++i) {
    set_accel_z(i);
    app.Step();
    b.clock.Advance(hvc::App::kImuPeriodMs);
  }
  set_accel_z(i);  // the step that runs /imu takes one more sample
  const std::string out = b.Run(app, "/imu");
  EXPECT_NE(out.find("z=1000 mg"), std::string::npos) << out;
}

TEST(HvcBench, ImuReportsWrongDeviceId) {
  Bench b;
  b.imu.regs[hvc::Asm330lhb::kRegWhoAmI] = 0xFF;  // floating MISO
  hvc::App app(b.Peripherals());
  const std::string out = b.Run(app, "/imu");
  EXPECT_NE(out.find("device ID=0xFF (expect 0x6B) WRONG, no data"),
            std::string::npos);
}

TEST(HvcBench, StreamsLegsPeriodicallyUntilStopped) {
  if (!hvc::App::kStreamLegs) {
    GTEST_SKIP() << "kStreamLegs is off";
  }
  Bench b;
  hvc::App app(b.Peripherals());
  b.leg2.Write(true);
  b.console.TakeTx();

  app.Step();  // first step streams immediately: legs only, no IMU
  std::vector<uint8_t> tx = b.console.TakeTx();
  std::string out(tx.begin(), tx.end());
  EXPECT_NE(out.find("SD2 = OK  SD3 = BROKEN  SD4 = BROKEN  SD5 = BROKEN  "
                     "SD15 = BROKEN"),
            std::string::npos)
      << out;
  EXPECT_EQ(out.find("ID="), std::string::npos);

  b.clock.Advance(hvc::App::kStreamPeriodMs / 2);
  app.Step();  // too soon for the next line
  tx = b.console.TakeTx();
  EXPECT_EQ(std::string(tx.begin(), tx.end()).find("SD2"), std::string::npos);

  b.Run(app, "/stream off");
  b.clock.Advance(hvc::App::kStreamPeriodMs * 4);
  app.Step();
  tx = b.console.TakeTx();
  EXPECT_EQ(std::string(tx.begin(), tx.end()).find("SD2"), std::string::npos);
}

TEST(HvcBench, BmsReportsEachBmbAndHowManyResponded) {
  Bench b;
  hvc::App app(b.Peripherals());
  b.bms.ids[0].pec_ok = true;
  b.bms.ids[0].command_counter = 3;
  const uint8_t sid[6] = {0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC};
  std::memcpy(b.bms.ids[0].sid, sid, sizeof(sid));
  const std::string out = b.Run(app, "/bms");
  EXPECT_EQ(b.bms.reads, 1);
  EXPECT_NE(out.find("BMB  1: SID=123456789ABC  cmd_cnt=3  PEC OK"),
            std::string::npos)
      << out;
  // Every other BMB in the chain (if kNumBmbs > 1) failed its PEC.
  const std::string summary = "1/" + std::to_string(hvc::kNumBmbs) +
                              " BMBs responded";
  EXPECT_NE(out.find(summary), std::string::npos) << out;
  if (hvc::kNumBmbs > 1) {
    EXPECT_NE(out.find("cmd_cnt=0  PEC FAIL"), std::string::npos);
  }
}

TEST(HvcBench, ProbesBmsChainEverySecondAfterFiveSeconds) {
  if (!hvc::App::kStreamBms) {
    GTEST_SKIP() << "kStreamBms is off";
  }
  Bench b;
  hvc::App app(b.Peripherals());
  b.bms.ids[0].pec_ok = true;

  // Before 5 s: legs stream, but the chain is left alone.
  for (uint32_t t = 0; t < hvc::App::kBmsProbeStartMs;
       t += hvc::App::kStreamPeriodMs) {
    app.Step();
    b.clock.Advance(hvc::App::kStreamPeriodMs);
  }
  EXPECT_EQ(b.bms.reads, 0);

  b.console.TakeTx();
  app.Step();  // t = 5 s: first probe
  std::vector<uint8_t> tx = b.console.TakeTx();
  EXPECT_EQ(b.bms.reads, 1);
  EXPECT_EQ(b.bms.configures, 1);
  EXPECT_EQ(b.bms.cell_reads, 1);
  EXPECT_NE(std::string(tx.begin(), tx.end()).find("BMBs responded"),
            std::string::npos);

  b.clock.Advance(hvc::App::kStreamPeriodMs);  // 5.5 s: legs only
  app.Step();
  EXPECT_EQ(b.bms.reads, 1);
  b.clock.Advance(hvc::App::kStreamPeriodMs);  // 6 s: next probe
  app.Step();
  EXPECT_EQ(b.bms.reads, 2);
}

TEST(HvcBench, ImdReadsInput) {
  Bench b;
  hvc::App app(b.Peripherals());
  b.imd_error.Write(true);
  EXPECT_NE(b.Run(app, "/imd").find("imd_error=1"), std::string::npos);
}

}  // namespace
