#include <gtest/gtest.h>

#include "hmi/status_led.hpp"

namespace {
using lhre::can::VcuFaultCode;
using lhre::can::VcuState;

TEST(StatusLed, NormalStatesHaveDistinctColorsAndReadinessPatterns) {
  const auto idle = vcu::StatusColor(VcuState::kIdle, VcuFaultCode::kNone, 0);
  EXPECT_EQ(idle.r, 0);
  EXPECT_EQ(idle.g, 0);
  EXPECT_GT(idle.b, 0);
  const auto precharge =
      vcu::StatusColor(VcuState::kPrecharge, VcuFaultCode::kNone, 0);
  EXPECT_GT(precharge.r, precharge.g);
  EXPECT_GT(precharge.g, 0);
  EXPECT_EQ(precharge.b, 0);
  const auto precharge_off =
      vcu::StatusColor(VcuState::kPrecharge, VcuFaultCode::kNone, 500);
  EXPECT_EQ(precharge_off.r + precharge_off.g + precharge_off.b, 0);
  const auto hv =
      vcu::StatusColor(VcuState::kTsActive, VcuFaultCode::kNone, 500);
  EXPECT_GT(hv.r, 0);
  EXPECT_GT(hv.g, 0);
  EXPECT_EQ(hv.b, 0);
  const auto ready =
      vcu::StatusColor(VcuState::kRtdWait, VcuFaultCode::kNone, 0);
  const auto ready_off =
      vcu::StatusColor(VcuState::kRtdWait, VcuFaultCode::kNone, 500);
  EXPECT_EQ(ready.r, 0);
  EXPECT_GT(ready.g, 0);
  EXPECT_EQ(ready.b, 0);
  EXPECT_EQ(ready_off.r + ready_off.g + ready_off.b, 0);
  const auto drive =
      vcu::StatusColor(VcuState::kDrive, VcuFaultCode::kNone, 500);
  EXPECT_EQ(drive.r, 0);
  EXPECT_GT(drive.g, 0);
  EXPECT_EQ(drive.b, 0);
}

TEST(StatusLed, FaultCodeOverridesIdleWithCountedRedPulsesAndPause) {
  const auto code = static_cast<VcuFaultCode>(2);
  for (uint32_t t : {0u, 500u}) {
    const auto c = vcu::StatusColor(VcuState::kIdle, code, t);
    EXPECT_GT(c.r, 0);
    EXPECT_EQ(c.g, 0);
    EXPECT_EQ(c.b, 0);
  }
  for (uint32_t t : {250u, 750u, 1000u, 2000u}) {
    const auto c = vcu::StatusColor(VcuState::kIdle, code, t);
    EXPECT_EQ(c.r + c.g + c.b, 0);
  }
}

TEST(StatusLed, BspdAlwaysWinsAndFaultWithoutCodeStaysRed) {
  const auto bspd = vcu::StatusColor(VcuState::kBspdTripped,
                                     static_cast<VcuFaultCode>(2), 750);
  EXPECT_GT(bspd.r, 0);
  EXPECT_EQ(bspd.r, bspd.b);
  EXPECT_EQ(bspd.g, 0);
  const auto fault =
      vcu::StatusColor(VcuState::kFault, VcuFaultCode::kNone, 750);
  EXPECT_GT(fault.r, 0);
  EXPECT_EQ(fault.g, 0);
  EXPECT_EQ(fault.b, 0);
  const auto boot = vcu::StatusColor(VcuState::kInit, VcuFaultCode::kNone, 0);
  EXPECT_GT(boot.r, 0);
  EXPECT_EQ(boot.r, boot.g);
  EXPECT_EQ(boot.r, boot.b);
}
}  // namespace
