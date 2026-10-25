#include <string>

#include <gtest/gtest.h>

#include "host_uart.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/uart.hpp"
#include "mvp.hpp"

namespace {
int resets = 0;
void Reset() { ++resets; }

struct Bench {
  lhal::host::TestClock clock;
  lhal::host::Pwm r, g, b;
  longhorn::RgbLed led{&r, &g, &b};
  vcu::host::Uart usb;
  vcu::Mvp app{&clock, &usb, &led, &Reset};
  Bench() { resets = 0; }
  void Type(const std::string& bytes) {
    usb.InjectRx(reinterpret_cast<const uint8_t*>(bytes.data()), bytes.size());
  }
};

TEST(Mvp, RainbowRunsWithoutVehicleOrScheduler) {
  Bench f;
  EXPECT_FLOAT_EQ(f.r.duty(), 0.5f);
  f.clock.Advance(33);
  f.app.Step();
  EXPECT_GT(f.g.duty(), 0);
  EXPECT_FLOAT_EQ(f.b.duty(), 0);
  const float red = f.r.duty();
  f.clock.Advance(33);
  f.app.Step();
  EXPECT_LT(f.r.duty(), red);
}

TEST(Mvp, ManualColorHoldsAndRainbowCanResume) {
  Bench f;
  f.Type("/led red\n");
  f.app.Step();
  EXPECT_FLOAT_EQ(f.r.duty(), 0.25f);
  EXPECT_FLOAT_EQ(f.g.duty(), 0);
  EXPECT_FLOAT_EQ(f.b.duty(), 0);
  f.clock.Advance(100);
  f.app.Step();
  EXPECT_FLOAT_EQ(f.r.duty(), 0.25f);
  f.Type("/led auto\n");
  f.app.Step();
  f.clock.Advance(33);
  f.app.Step();
  EXPECT_GT(f.g.duty(), 0);
  f.Type("/led off\n");
  f.app.Step();
  EXPECT_FLOAT_EQ(f.r.duty(), 0);
  EXPECT_FLOAT_EQ(f.g.duty(), 0);
  EXPECT_FLOAT_EQ(f.b.duty(), 0);
}

TEST(Mvp, ReceiveHookDefersSplitNewlineFreeDfuToMainLoop) {
  Bench f;
  f.Type("upda");
  f.app.Step();
  EXPECT_EQ(resets, 0);
  f.Type("te.");
  EXPECT_EQ(resets, 0);
  f.app.Step();
  EXPECT_EQ(resets, 1);
}

TEST(Mvp, ShellReportsVersionAndAcceptsDfu) {
  Bench f;
  f.Type("/version\n");
  f.app.Step();
  auto bytes = f.usb.TakeTx();
  EXPECT_NE(std::string(bytes.begin(), bytes.end()).find("VCU-MVP"),
            std::string::npos);
  f.Type("/dfu\n");
  f.app.Step();
  EXPECT_EQ(resets, 1);
}

TEST(Mvp, PolledStreamAlsoAcceptsRawDfu) {
  lhal::host::TestClock clock;
  lhal::host::Pwm r, g, b;
  longhorn::RgbLed led(&r, &g, &b);
  lhal::host::Uart usb;
  vcu::Mvp app(&clock, &usb, &led, &Reset);
  resets = 0;
  const std::string command = "update.";
  usb.InjectRx(reinterpret_cast<const uint8_t*>(command.data()),
               command.size());
  app.Step();
  EXPECT_EQ(resets, 1);
}
}  // namespace
