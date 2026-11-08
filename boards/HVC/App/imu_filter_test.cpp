// Host tests for the IMU moving-average filter.

#include "imu_filter.hpp"

#include <gtest/gtest.h>

namespace {

TEST(MovingAverage, AveragesWhatItHasUntilFull) {
  hvc::MovingAverage<4> f;
  EXPECT_EQ(f.Push(100), 100);  // 100 / 1
  EXPECT_EQ(f.Push(200), 150);  // 300 / 2
  EXPECT_EQ(f.Push(300), 200);  // 600 / 3
  EXPECT_EQ(f.Push(400), 250);  // 1000 / 4
  EXPECT_EQ(f.Push(500), 350);  // oldest (100) drops out: 1400 / 4
}

TEST(MovingAverage, SmoothsAlternatingNoiseToItsMean) {
  hvc::MovingAverage<8> f;
  int32_t out = 0;
  for (int i = 0; i < 16; ++i) {
    out = f.Push(i % 2 == 0 ? 1100 : 900);  // 1000 ± 100 noise
  }
  EXPECT_EQ(out, 1000);
}

TEST(MovingAverage, HandlesNegativeValues) {
  hvc::MovingAverage<2> f;
  f.Push(-1000);
  EXPECT_EQ(f.Push(-2000), -1500);
}

TEST(ImuFilter, FiltersEveryChannelIndependently) {
  hvc::ImuFilter filter;
  hvc::ImuSample a;
  a.accel_mg[2] = 1100;
  a.gyro_mdps[0] = -200;
  a.temp_centi_c = 2400;
  hvc::ImuSample b;
  b.accel_mg[2] = 900;
  b.gyro_mdps[0] = 200;
  b.temp_centi_c = 2600;

  filter.Push(a);
  const hvc::ImuSample out = filter.Push(b);
  EXPECT_EQ(out.accel_mg[2], 1000);
  EXPECT_EQ(out.accel_mg[0], 0);
  EXPECT_EQ(out.gyro_mdps[0], 0);
  EXPECT_EQ(out.temp_centi_c, 2500);
}

}  // namespace
