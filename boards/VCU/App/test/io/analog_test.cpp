#include <gtest/gtest.h>

#include "config/params.hpp"
#include "io/analog.hpp"
#include "io/peripherals.hpp"
#include "lhal/host/adc.hpp"

namespace {

const vcu::Params& P() { return vcu::ParamsFor(vcu::kDefaultEventMode); }

TEST(Analog, VddaFromVrefint) {
  bool from = false;
  // 3.0 V x 1650 / 1500 = 3.3 V.
  EXPECT_NEAR(vcu::ComputeVdda(1500, 1650, 4095, P().analog, &from), 3.3f,
              1e-4f);
  EXPECT_TRUE(from);
  // A sagging rail reads higher VREFINT counts.
  EXPECT_NEAR(vcu::ComputeVdda(1650, 1650, 4095, P().analog, &from), 3.0f,
              1e-4f);
  // Missing calibration or reading falls back to nominal.
  EXPECT_EQ(vcu::ComputeVdda(1500, 0, 4095, P().analog, &from), 3.3f);
  EXPECT_FALSE(from);
  EXPECT_EQ(vcu::ComputeVdda(0, 1650, 4095, P().analog, &from), 3.3f);
  // Implausible result (e.g. VREFINT channel misconfigured) too.
  EXPECT_EQ(vcu::ComputeVdda(100, 1650, 4095, P().analog, &from), 3.3f);
  EXPECT_FALSE(from);
}

TEST(Analog, DividerMathMatchesTheSchematic) {
  lhal::host::AdcScan adc1(vcu::kAdc1Channels);
  lhal::host::AdcScan adc2(vcu::kAdc2Channels);
  adc1.Set(vcu::kAdc1Vrefint, 1500);
  // 4.5 V sensor -> 2.980 V pin through 5.1k/10k.
  adc1.SetVolts(vcu::kAdc1Apps1, 4.5f * 10.0f / 15.1f, 3.3f);
  // 24 V loop -> 2.89 V pin through 100k/13.7k.
  adc2.SetVolts(vcu::kAdc2ShutdownIn, 24.0f * 13.7f / 113.7f, 3.3f);
  const vcu::AnalogReadings r = vcu::ReadAnalog(&adc1, &adc2, 1650, P().analog);
  EXPECT_NEAR(r.apps1.pin_v, 2.980f, 0.002f);
  EXPECT_NEAR(r.apps1.source_v, 4.5f, 0.005f);
  EXPECT_NEAR(r.shutdown_in.pin_v, 2.892f, 0.002f);
  EXPECT_NEAR(r.shutdown_in.source_v, 24.0f, 0.03f);
}

TEST(Analog, NullBanksReadZero) {
  const vcu::AnalogReadings r =
      vcu::ReadAnalog(nullptr, nullptr, 1650, P().analog);
  EXPECT_EQ(r.apps1.counts, 0);
  EXPECT_EQ(r.vdda, 3.3f);
}

TEST(Adc, ConstantValuesAreFreshButFrozenAcquisitionIsNot) {
  lhal::host::AdcScan adc(1);
  vcu::AdcProgress progress;
  EXPECT_FALSE(progress.Fresh(&adc, 0));
  adc.CompleteScan();
  EXPECT_TRUE(progress.Fresh(&adc, 1));
  EXPECT_TRUE(progress.Fresh(&adc, 10));
  EXPECT_FALSE(progress.Fresh(&adc, 11));
  adc.CompleteScan();
  EXPECT_TRUE(progress.Fresh(&adc, 12));
  EXPECT_FALSE(progress.Fresh(nullptr, 22));
}

}  // namespace
