// Tests for the bring-up test image (vcu::TestApp) through Step().

#include <gtest/gtest.h>

#include <memory>
#include <string>

#include "bringup/test_app.hpp"
#include "lhre_can_bench.hpp"
#include "lhre_can_vcu.hpp"
#include "test/bench.hpp"

namespace {

using lhre::can::BenchCommand;
using vcu::bench::Bench;
namespace bench = lhre::can::bench;
namespace msg = lhre::can::vcu;

class TestImage : public ::testing::Test {
 protected:
  void SetUp() override {
    app_ = std::make_unique<vcu::TestApp>(b_.peripherals());
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

  Bench b_;
  std::unique_ptr<vcu::TestApp> app_;
};

TEST_F(TestImage, StreamsAnalogAndDigitalWithoutVehicleFrames) {
  Run(50);
  bool counts = false, volts = false, cal = false, io = false;
  lhal::CanFrame f;
  while (b_.logger.Receive(&f)) {
    counts |= msg::VcuAdcCountsA::Matches(f.id);
    volts |= msg::VcuAdcVoltsB::Matches(f.id);
    cal |= msg::VcuCalibratedA::Matches(f.id);
    io |= msg::VcuDigitalIo::Matches(f.id);
  }
  EXPECT_TRUE(counts && volts && cal && io);
  // Nothing at all on the Critical bus: no torque commands.
  EXPECT_TRUE(b_.vcu_critical.sent().empty());
}

TEST_F(TestImage, ReportsDigitalInputs) {
  b_.latch_state.Write(true);
  b_.PressRtd(true);
  Run(20);
  msg::VcuDigitalIo io;
  ASSERT_TRUE(b_.LastFrame(b_.logger, &io));
  EXPECT_TRUE(io.inputs_latch_state);
  EXPECT_TRUE(io.inputs_rtd_switch);
  EXPECT_FALSE(io.inputs_b1_disconnect);
}

TEST_F(TestImage, SharedRainbowRunsWithoutVehicleCanAndResumesAfterOverride) {
  Run(20);
  const float red = b_.led_r.duty();
  Run(500);
  EXPECT_NE(b_.led_r.duty(), red);
  Shell("/led 100 0 0");
  Run(20);
  EXPECT_FLOAT_EQ(b_.led_r.duty(), 1);
  EXPECT_FLOAT_EQ(b_.led_g.duty(), 0);
  Shell("/led auto");
  Run(40);
  EXPECT_GT(b_.led_g.duty(), 0);
}

TEST_F(TestImage, DrivesOutputsFromShellAndCan) {
  Run(20);
  EXPECT_NE(Shell("/out linelock 1").find("linelock=1"), std::string::npos);
  Run(20);
  EXPECT_TRUE(b_.linelock_ctrl.Read());

  bench::BenchTestCommand c;
  c.command = BenchCommand::kSetOutputs;
  c.output_mask_bms_led = true;
  c.output_mask_crit_stb = true;
  c.output_values = (0u << 2) | (1u << 4);  // bms off, crit_stb on
  b_.logger.Send(c.ToFrame());
  Run(20);
  EXPECT_FALSE(b_.bms_led.Read());
  EXPECT_TRUE(b_.crit_stb.Read());
  EXPECT_TRUE(b_.linelock_ctrl.Read());  // untouched by the mask

  Shell("/release");
  Run(20);
  EXPECT_FALSE(b_.linelock_ctrl.Read());
  EXPECT_TRUE(b_.bms_led.Read());
  EXPECT_FALSE(b_.crit_stb.Read());
}

TEST_F(TestImage, ToneIsCappedLocally) {
  Run(20);
  bench::BenchTestCommand c;
  c.command = BenchCommand::kPlayTone;
  c.tone_frequency = 2000;
  c.set_tone_amplitude(0.5f);
  c.tone_duration = 10000;  // far past the ceiling
  b_.logger.Send(c.ToFrame());
  Run(20);
  EXPECT_TRUE(b_.dac.playing());
  EXPECT_TRUE(b_.amp_shutdown.Read());
  EXPECT_EQ(b_.dac.sample_rate_hz(), 64000u);
  Run(1500);
  EXPECT_FALSE(b_.dac.playing());
  EXPECT_FALSE(b_.amp_shutdown.Read());
}

TEST_F(TestImage, EchoesOnEachBus) {
  Run(20);
  bench::BenchEchoRequestCritical crit;
  crit.payload = 0x1122334455667788ull;
  b_.car.Send(crit.ToFrame());
  bench::BenchEchoRequestData data;
  data.payload = 0xCAFEull;
  b_.logger.Send(data.ToFrame());
  Run(20);
  msg::VcuEchoReplyCritical crit_reply;
  ASSERT_TRUE(b_.LastFrame(b_.car, &crit_reply));
  EXPECT_EQ(crit_reply.payload, crit.payload);
  msg::VcuEchoReplyData data_reply;
  ASSERT_TRUE(b_.LastFrame(b_.logger, &data_reply));
  EXPECT_EQ(data_reply.payload, data.payload);
}

TEST_F(TestImage, WatchdogTestStopsRefreshAndSafesOutputs) {
  Run(100);
  ASSERT_TRUE(b_.watchdog.started());
  Shell("/out amp 1");
  Run(20);
  ASSERT_TRUE(b_.amp_shutdown.Read());
  EXPECT_NE(Shell("/wdtest").find("iwdg"), std::string::npos);
  const uint32_t refreshes = b_.watchdog.refresh_count();
  Run(25);
  EXPECT_FALSE(b_.amp_shutdown.Read());
  Run(200);
  EXPECT_EQ(b_.watchdog.refresh_count(), refreshes);
}

TEST_F(TestImage, CalibrationCapture) {
  Run(20);
  Shell("/cal start");
  b_.SetPedals(0.6f, 0.6f);
  Run(50);
  b_.SetPedals(4.4f, 4.4f);
  Run(50);
  const std::string out = Shell("/cal stop");
  EXPECT_NE(out.find("apps1"), std::string::npos) << out;
  EXPECT_NE(out.find("max  4"), std::string::npos) << out;
}

TEST_F(TestImage, ReportsResetCause) {
  EXPECT_NE(Shell("/info").find("power-on"), std::string::npos);
  Run(120);
  msg::VcuDiag d;
  ASSERT_TRUE(b_.LastFrame(b_.logger, &d));
  EXPECT_EQ(d.reset_cause, lhre::can::ResetCause::kPowerOn);
}

}  // namespace
