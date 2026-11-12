// The bring-up image's VCP shell (bringup/test_shell.cpp): every command's
// output and its argument checking. A wrong /out on the bench drives a
// real pin, so bad input must change nothing.

#include <gtest/gtest.h>

#include <cstdio>
#include <memory>
#include <string>

#include "bringup/test_app.hpp"
#include "test/bench.hpp"

namespace {

using vcu::bench::Bench;

bool Has(const std::string& s, const std::string& part) {
  return s.find(part) != std::string::npos;
}

int Occurrences(const std::string& s, const std::string& part) {
  int n = 0;
  for (size_t at = s.find(part); at != std::string::npos;
       at = s.find(part, at + part.size())) {
    ++n;
  }
  return n;
}

class TestShell : public ::testing::Test {
 protected:
  void SetUp() override {
    app_ = std::make_unique<vcu::TestApp>(b_.peripherals());
    Run(20);
  }

  void Run(uint32_t ms) {
    for (uint32_t i = 0; i < ms; ++i) {
      app_->Step();
      b_.clock.Advance(1);
    }
  }

  std::string Shell(const std::string& line) {
    b_.vcp.TakeTx();
    const std::string in = line + "\r";
    b_.vcp.InjectRx(reinterpret_cast<const uint8_t*>(in.data()), in.size());
    app_->Step();
    const auto tx = b_.vcp.TakeTx();
    return std::string(tx.begin(), tx.end());
  }

  std::string TakeVcp() {
    const auto tx = b_.vcp.TakeTx();
    return std::string(tx.begin(), tx.end());
  }

  Bench b_;
  std::unique_ptr<vcu::TestApp> app_;
};

TEST_F(TestShell, OutDrivesEachNamedPin) {
  const std::pair<const char*, lhal::host::Gpio*> pins[] = {
      {"linelock", &b_.linelock_ctrl},
      {"amp", &b_.amp_shutdown},
      {"bms", &b_.bms_led},
      {"imd", &b_.imd_led},
      {"crit_stb", &b_.crit_stb},
      {"data_stb", &b_.data_stb},
  };
  for (const auto& [name, pin] : pins) {
    SCOPED_TRACE(name);
    const std::string n = name;
    EXPECT_TRUE(Has(Shell("/out " + n + " 1"), n + "=1"));
    Run(20);
    EXPECT_TRUE(pin->Read());
    EXPECT_TRUE(Has(Shell("/out " + n + " 0"), n + "=0"));
    Run(20);
    EXPECT_FALSE(pin->Read());
  }
}

TEST_F(TestShell, OutWithBadArgumentsChangesNothing) {
  for (const char* line :
       {"/out", "/out linelock", "/out linelock 2", "/out linelock x",
        "/out line 1", "/out linelocks 1", "/out bogus 1", "/out  1"}) {
    SCOPED_TRACE(line);
    EXPECT_TRUE(Has(Shell(line), "usage: /out"));
    Run(20);
    EXPECT_FALSE(b_.linelock_ctrl.Read());
    EXPECT_FALSE(b_.amp_shutdown.Read());
    EXPECT_TRUE(b_.bms_led.Read());
    EXPECT_TRUE(b_.imd_led.Read());
    EXPECT_FALSE(b_.crit_stb.Read());
    EXPECT_FALSE(b_.data_stb.Read());
  }
}

TEST_F(TestShell, AdcListsEveryChannel) {
  const std::string out = Shell("/adc");
  EXPECT_TRUE(Has(out, "vdda=3300mV (vrefint)")) << out;
  for (const char* ch :
       {"apps1", "apps2", "bse1", "bse2", "steer", "shdn_in", "shdn_out"}) {
    EXPECT_TRUE(Has(out, ch)) << ch;
  }
  // The 24 V bench loop reads as source volts, not the ~2.9 V at the pin.
  unsigned counts = 0;
  int pin_mv = 0, source_mv = 0;
  ASSERT_EQ(std::sscanf(out.c_str() + out.find("shdn_in"),
                        "shdn_in %u counts %d mV pin %d mV source", &counts,
                        &pin_mv, &source_mv),
            3)
      << out;
  EXPECT_NEAR(source_mv, 24000, 20);
  EXPECT_NEAR(pin_mv, 2891, 20);
  EXPECT_TRUE(Has(out, "cal: apps1")) << out;
}

TEST_F(TestShell, IoShowsInputsAndOutputs) {
  b_.b2_overvoltage.Write(true);
  b_.PressRtd(true);
  Shell("/out data_stb 1");
  Run(20);
  const std::string out = Shell("/io");
  EXPECT_TRUE(Has(out, "B2_Overvoltage=1")) << out;
  EXPECT_TRUE(Has(out, "B1_Disconnect=0")) << out;
  EXPECT_TRUE(Has(out, "rtd=1")) << out;
  EXPECT_TRUE(Has(out, "data_stb=1")) << out;
  EXPECT_TRUE(Has(out, "crit_stb=0")) << out;
}

TEST_F(TestShell, ToneRejectsBadArgumentsAndStopsOnOff) {
  for (const char* line : {"/tone 0 50 100", "/tone -5 50 100",
                           "/tone 1000 -1 100", "/tone 1000 50 0"}) {
    SCOPED_TRACE(line);
    EXPECT_TRUE(Has(Shell(line), "usage: /tone"));
    Run(20);
    EXPECT_FALSE(b_.dac.playing());
  }
  EXPECT_TRUE(Has(Shell("/tone 1000 50 5000"), "tone 1000 Hz 50% 5000 ms"));
  Run(20);
  EXPECT_TRUE(b_.dac.playing());
  EXPECT_TRUE(Has(Shell("/tone off"), "tone off"));
  Run(20);
  EXPECT_FALSE(b_.dac.playing());
}

TEST_F(TestShell, LedClampsEachChannelAndRejectsMissingOnes) {
  EXPECT_TRUE(Has(Shell("/led 10 20"), "usage: /led"));
  Shell("/led 250 -10 100");
  Run(40);
  EXPECT_FLOAT_EQ(b_.led_r.duty(), 1.0f);
  EXPECT_FLOAT_EQ(b_.led_g.duty(), 0.0f);
  EXPECT_FLOAT_EQ(b_.led_b.duty(), 1.0f);
}

TEST_F(TestShell, ReleaseReturnsEverythingToSafe) {
  Shell("/out linelock 1");
  Shell("/out bms 0");
  Shell("/tone 1000 50 5000");
  Shell("/led 100 0 0");
  Run(20);
  EXPECT_TRUE(Has(Shell("/release"), "safe state"));
  Run(40);
  EXPECT_FALSE(b_.linelock_ctrl.Read());
  EXPECT_TRUE(b_.bms_led.Read());
  EXPECT_FALSE(b_.dac.playing());
  EXPECT_FALSE(b_.amp_shutdown.Read());
}

TEST_F(TestShell, StreamIsFlooredAtTheIoPeriodAndStops) {
  EXPECT_TRUE(Has(Shell("/stream 1"), "stream 10 ms"));
  TakeVcp();
  Run(200);
  // One line per 10 ms I/O tick, not one per millisecond.
  const int lines = Occurrences(TakeVcp(), "adc ");
  EXPECT_GE(lines, 18);
  EXPECT_LE(lines, 21);

  EXPECT_TRUE(Has(Shell("/stream 50"), "stream 50 ms"));
  TakeVcp();
  Run(200);
  EXPECT_NEAR(Occurrences(TakeVcp(), "adc "), 4, 1);

  EXPECT_TRUE(Has(Shell("/stream 0"), "stream 0 ms"));
  TakeVcp();
  Run(200);
  EXPECT_EQ(Occurrences(TakeVcp(), "adc "), 0);
}

TEST_F(TestShell, CalShowsNothingBeforeACaptureAndChecksItsVerb) {
  const std::string empty = Shell("/cal show");
  EXPECT_TRUE(Has(empty, "0 samples")) << empty;
  EXPECT_FALSE(Has(empty, "apps1")) << empty;
  EXPECT_TRUE(Has(Shell("/cal go"), "usage: /cal"));

  Shell("/cal start");
  Run(50);
  const std::string during = Shell("/cal show");
  EXPECT_TRUE(Has(during, "apps1")) << during;
  // show doesn't stop the capture; stop does.
  Shell("/cal stop");
  const std::string after = Shell("/cal show");
  Run(50);
  EXPECT_EQ(Shell("/cal show"), after);
}

TEST_F(TestShell, InfoReportsEachBusSeparately) {
  lhal::CanStats crit;
  crit.bus_off_count = 3;
  crit.tx_dropped = 4;
  crit.rx_lost = 5;
  crit.tx_error_count = 6;
  crit.rx_error_count = 7;
  b_.vcu_critical.set_stats(crit);
  lhal::CanStats data;
  data.bus_off = true;
  b_.vcu_data.set_stats(data);
  const std::string out = Shell("/info");
  EXPECT_TRUE(Has(out, "critical: bus_off=3 tx_drop=4 rx_lost=5 tec=6 rec=7"))
      << out;
  EXPECT_TRUE(Has(out,
                  "data:     bus_off=0 tx_drop=0 rx_lost=0 tec=0 rec=0 "
                  "BUS-OFF"))
      << out;
  EXPECT_EQ(Occurrences(out, "BUS-OFF"), 1) << out;
}

}  // namespace
