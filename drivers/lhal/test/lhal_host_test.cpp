// Tests for the LHAL host backend.

#include <cstring>

#include <gtest/gtest.h>

#include "lhal/host/adc.hpp"
#include "lhal/host/can.hpp"
#include "lhal/host/dac.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/i2c.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "lhal/host/watchdog.hpp"
#include "lhal/reset.hpp"

namespace {

TEST(Gpio, WriteReadToggle) {
  lhal::host::Gpio pin;
  EXPECT_FALSE(pin.Read());
  pin.Write(true);
  EXPECT_TRUE(pin.Read());
  pin.Toggle();
  EXPECT_FALSE(pin.Read());
}

TEST(Pwm, StoresAndClampsDuty) {
  lhal::host::Pwm pwm;
  EXPECT_FLOAT_EQ(pwm.duty(), 0.0f);
  pwm.SetDuty(0.25f);
  EXPECT_FLOAT_EQ(pwm.duty(), 0.25f);
  pwm.SetDuty(1.5f);
  EXPECT_FLOAT_EQ(pwm.duty(), 1.0f);
  pwm.SetDuty(-0.5f);
  EXPECT_FLOAT_EQ(pwm.duty(), 0.0f);
}

TEST(Uart, WriteAccumulatesTx) {
  lhal::host::Uart uart;
  const uint8_t msg[] = {'h', 'i'};
  EXPECT_TRUE(lhal::IsOk(uart.Write(msg, sizeof(msg), 100)));
  ASSERT_EQ(uart.tx_data().size(), 2u);
  EXPECT_EQ(uart.TakeTx()[0], 'h');
  EXPECT_TRUE(uart.tx_data().empty());
}

TEST(Uart, ReadConsumesInjectedRx) {
  lhal::host::Uart uart;
  const uint8_t rx[] = {1, 2, 3};
  uart.InjectRx(rx, sizeof(rx));

  uint8_t buf[3] = {};
  EXPECT_TRUE(lhal::IsOk(uart.Read(buf, 3, 100)));
  EXPECT_EQ(std::memcmp(buf, rx, 3), 0);

  // Nothing left: mirrors the HAL by returning kTimeout.
  EXPECT_EQ(uart.Read(buf, 1, 100), lhal::Status::kTimeout);
}

TEST(Uart, AsyncCompletesImmediately) {
  lhal::host::Uart uart;
  const uint8_t msg[] = {'h', 'i'};
  bool done = false;
  uart.WriteAsync(
      msg, sizeof(msg),
      [](void* ctx, lhal::Status s) {
        *static_cast<bool*>(ctx) = lhal::IsOk(s);
      },
      &done);
  EXPECT_TRUE(done);
}

class FakeSensor : public lhal::host::I2cDevice {
 public:
  lhal::Status OnWrite(const uint8_t* data, size_t len) override {
    if (len >= 1) {
      reg_ = data[0];
    }
    return lhal::Status::kOk;
  }
  lhal::Status OnRead(uint8_t* data, size_t len) override {
    for (size_t i = 0; i < len; ++i) {
      data[i] = static_cast<uint8_t>(reg_ + i);
    }
    return lhal::Status::kOk;
  }

 private:
  uint8_t reg_ = 0;
};

TEST(I2c, RegisterReadThroughFakeDevice) {
  lhal::host::I2c bus;
  FakeSensor sensor;
  bus.Attach(0x48, &sensor);

  const uint8_t reg = 0x10;
  uint8_t value[2] = {};
  ASSERT_TRUE(lhal::IsOk(bus.WriteRead(0x48, &reg, 1, value, 2, 100)));
  EXPECT_EQ(value[0], 0x10);
  EXPECT_EQ(value[1], 0x11);
}

TEST(I2c, MissingDeviceNacks) {
  lhal::host::I2c bus;
  const uint8_t reg = 0x10;
  EXPECT_EQ(bus.Write(0x21, &reg, 1, 100), lhal::Status::kError);
}

TEST(Can, FramesReachPeersButNotSender) {
  lhal::host::CanNetwork network;
  lhal::host::Can node_a(&network);
  lhal::host::Can node_b(&network);

  lhal::CanFrame frame;
  frame.id = 0x123;
  frame.len = 2;
  frame.data[0] = 0xAB;
  frame.data[1] = 0xCD;
  EXPECT_TRUE(lhal::IsOk(node_a.Send(frame)));
  EXPECT_EQ(node_a.sent().size(), 1u);

  lhal::CanFrame rx;
  ASSERT_TRUE(node_b.Receive(&rx));
  EXPECT_EQ(rx.id, 0x123u);
  EXPECT_EQ(rx.len, 2u);
  EXPECT_EQ(rx.data[1], 0xCD);
  EXPECT_FALSE(node_b.Receive(&rx));
  EXPECT_FALSE(node_a.Receive(&rx));
}

TEST(Can, RxCallbackConsumesFrames) {
  lhal::host::CanNetwork network;
  lhal::host::Can node_a(&network);
  lhal::host::Can node_b(&network);

  int callback_count = 0;
  node_b.SetRxCallback(
      [](void* ctx, const lhal::CanFrame&) { ++*static_cast<int*>(ctx); },
      &callback_count);

  lhal::CanFrame frame;
  frame.id = 0x123;
  node_a.Send(frame);
  EXPECT_EQ(callback_count, 1);

  lhal::CanFrame rx;
  EXPECT_FALSE(node_b.Receive(&rx));  // consumed by the callback
}

TEST(Clock, TestClockAdvances) {
  lhal::host::TestClock clock;
  EXPECT_EQ(clock.Millis(), 0u);
  clock.Advance(100);
  EXPECT_EQ(clock.Millis(), 100u);
  clock.DelayMs(50);
  EXPECT_EQ(clock.Millis(), 150u);
}

TEST(Clock, ElapsedMsIsWrapSafe) {
  EXPECT_TRUE(lhal::ElapsedMs(150, 100, 50));
  EXPECT_FALSE(lhal::ElapsedMs(149, 100, 50));
  EXPECT_TRUE(lhal::ElapsedMs(5, 0xFFFFFFF0u, 20));
}

TEST(AdcScan, SetReadAndClamp) {
  lhal::host::AdcScan adc(3);
  EXPECT_EQ(adc.channel_count(), 3u);
  EXPECT_EQ(adc.full_scale(), 4095);
  adc.Set(1, 1234);
  EXPECT_EQ(adc.Read(1), 1234);
  adc.Set(2, 9999);  // clamps to full scale
  EXPECT_EQ(adc.Read(2), 4095);
  EXPECT_EQ(adc.Read(7), 0);  // out of range reads 0
  adc.SetVolts(0, 1.65f, 3.3f);
  EXPECT_NEAR(adc.Read(0), 2048, 1);
}

TEST(WaveformDac, RecordsPlaybackAndStop) {
  lhal::host::WaveformDac dac;
  const uint16_t samples[] = {2048, 4095, 2048, 0};
  EXPECT_EQ(dac.Play(samples, 0, 1000), lhal::Status::kError);
  ASSERT_TRUE(lhal::IsOk(dac.Play(samples, 4, 32000)));
  EXPECT_TRUE(dac.playing());
  EXPECT_EQ(dac.samples().size(), 4u);
  EXPECT_EQ(dac.sample_rate_hz(), 32000u);
  dac.Stop(2048);
  EXPECT_FALSE(dac.playing());
  EXPECT_EQ(dac.idle_code(), 2048);
}

TEST(Watchdog, CountsStartAndRefresh) {
  lhal::host::Watchdog wdg;
  EXPECT_EQ(wdg.Start(0), lhal::Status::kError);
  ASSERT_TRUE(lhal::IsOk(wdg.Start(100)));
  EXPECT_TRUE(wdg.started());
  wdg.Refresh();
  wdg.Refresh();
  EXPECT_EQ(wdg.refresh_count(), 2u);
}

TEST(Can, StatsDefaultZeroAndSettable) {
  lhal::host::Can node;
  EXPECT_EQ(node.stats().bus_off_count, 0u);
  lhal::CanStats stats;
  stats.bus_off_count = 3;
  node.set_stats(stats);
  EXPECT_EQ(node.stats().bus_off_count, 3u);
}

TEST(ResetCause, Names) {
  EXPECT_STREQ(lhal::ToString(lhal::ResetCause::kIndependentWatchdog), "iwdg");
  EXPECT_STREQ(lhal::ToString(lhal::ResetCause::kUnknown), "unknown");
}

}  // namespace
