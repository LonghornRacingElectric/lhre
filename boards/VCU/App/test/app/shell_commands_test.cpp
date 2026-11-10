// The VCU's serial shell, driven through the app's Step() path. /mode is the
// only command that changes how the car drives, so most of this pins when
// a mode change takes effect and what bad input does.

#include <gtest/gtest.h>

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "app/vcu_app.hpp"
#include "lhre_can_vcu.hpp"
#include "safety/faults.hpp"
#include "test/bench.hpp"

namespace {

using lhre::can::EventMode;
using lhre::can::VcuFaultCode;
using VcuState = lhre::can::VcuStateMachine;
using vcu::bench::Bench;
namespace msg = lhre::can::vcu;

std::vector<std::string> Lines(const std::string& text) {
  std::vector<std::string> lines;
  std::string line;
  for (char c : text) {
    if (c == '\n') {
      lines.push_back(line);
      line.clear();
    } else if (c != '\r') {
      line += c;
    }
  }
  if (!line.empty()) {
    lines.push_back(line);
  }
  return lines;
}

bool IsNameChar(char c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
         (c >= '0' && c <= '9') || c == '_';
}

// The line holding `name` as a whole word, or "" if none does.
std::string LineNaming(const std::string& text, const std::string& name) {
  for (const std::string& line : Lines(text)) {
    for (size_t at = line.find(name); at != std::string::npos;
         at = line.find(name, at + 1)) {
      const size_t end = at + name.size();
      if ((at == 0 || !IsNameChar(line[at - 1])) &&
          (end == line.size() || !IsNameChar(line[end]))) {
        return line;
      }
    }
  }
  return "";
}

bool Contains(const std::string& text, const std::string& part) {
  return text.find(part) != std::string::npos;
}

class ShellCommandsTest : public ::testing::Test {
 protected:
  void SetUp() override { app_ = std::make_unique<vcu::App>(b_.peripherals()); }

  void ReachRtdWait() {
    b_.Energize();
    b_.SetBrakePsi(500.0f);
    b_.SetPedalTravel(0.0f);
    b_.Run(*app_, 200);
    ASSERT_EQ(app_->state(), VcuState::kRtdWait);
  }

  void ReachDrive() {
    ReachRtdWait();
    b_.PressRtd(true);
    b_.Run(*app_, 50);
    ASSERT_EQ(app_->state(), VcuState::kDrive);
  }

  std::string Shell(const std::string& line) {
    b_.vcp.TakeTx();
    const std::string in = line + "\r";
    b_.vcp.InjectRx(reinterpret_cast<const uint8_t*>(in.data()), in.size());
    app_->Step();
    const auto tx = b_.vcp.TakeTx();
    return std::string(tx.begin(), tx.end());
  }

  float InverterTorqueLimit() {
    msg::InverterTorqueCommand cmd;
    EXPECT_TRUE(b_.LastFrame(b_.car, &cmd));
    return cmd.torque_limit();
  }

  Bench b_;
  std::unique_ptr<vcu::App> app_;
};

TEST_F(ShellCommandsTest, ModeSwitchesOutsideDriveAndReachesTheInverter) {
  ReachRtdWait();
  EXPECT_TRUE(Contains(Shell("/mode 5"), "queued"));
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kMotorSpin);
  EXPECT_NEAR(InverterTorqueLimit(), 20.0f, 0.1f);
  msg::VcuState ds;
  ASSERT_TRUE(b_.LastFrame(b_.car, &ds));
  EXPECT_EQ(ds.event_mode, static_cast<uint8_t>(EventMode::kMotorSpin));
}

// Parameters never change under the driver's foot; the request waits.
TEST_F(ShellCommandsTest, ModeRequestInDriveWaitsUntilDriveEnds) {
  ReachDrive();
  Shell("/mode 5");
  b_.Run(*app_, 100);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
  EXPECT_NEAR(InverterTorqueLimit(), 230.0f, 0.1f);
  b_.PressRtd(false);
  b_.Run(*app_, 30);
  ASSERT_NE(app_->state(), VcuState::kDrive);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kMotorSpin);
  EXPECT_NEAR(InverterTorqueLimit(), 20.0f, 0.1f);
}

TEST_F(ShellCommandsTest, ModeLeavingMotorSpinRestoresTheCarLimit) {
  ReachRtdWait();
  Shell("/mode 5");
  b_.Run(*app_, 30);
  ASSERT_EQ(app_->snapshot().event_mode, EventMode::kMotorSpin);
  Shell("/mode 4");
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
  EXPECT_NEAR(InverterTorqueLimit(), 230.0f, 0.1f);
}

TEST_F(ShellCommandsTest, ModeZeroSelectsTheDefaults) {
  ReachRtdWait();
  Shell("/mode 0");
  b_.Run(*app_, 30);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kUnassigned);
}

// A typo must not switch modes, and the reply names the mode still in use.
TEST_F(ShellCommandsTest, BadModeArgumentsChangeNothing) {
  ReachRtdWait();
  const std::string current = lhre::can::ToString(EventMode::kEndurance);
  for (const char* line : {"/mode", "/mode 6", "/mode 9", "/mode 55", "/mode x",
                           "/mode -1", "/mode 5x", "/mode 5 1"}) {
    SCOPED_TRACE(line);
    const std::string out = Shell(line);
    EXPECT_TRUE(Contains(out, "usage: /mode 0..5")) << out;
    EXPECT_TRUE(Contains(out, "mode=" + current)) << out;
    EXPECT_FALSE(Contains(out, "queued")) << out;
    b_.Run(*app_, 10);
    EXPECT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
  }
}

TEST_F(ShellCommandsTest, StateShowsTheModeInUse) {
  ReachRtdWait();
  Shell("/mode 5");
  b_.Run(*app_, 30);
  const std::string out = Shell("/state");
  EXPECT_TRUE(Contains(
      out, std::string("mode=") + lhre::can::ToString(EventMode::kMotorSpin)))
      << out;
  EXPECT_TRUE(Contains(
      out, std::string("state=") + lhre::can::ToString(VcuState::kRtdWait)))
      << out;
}

// Every active fault gets its own line under its own name, and only the
// ones that cut torque say so.
void ExpectFaultListMatches(uint32_t faults, const std::string& out) {
  EXPECT_EQ(Contains(out, "no active faults"), faults == 0) << out;
  for (uint32_t i = 0; i < 32; ++i) {
    const auto code = static_cast<VcuFaultCode>(i + 1);
    const std::string name = lhre::can::ToString(code);
    if (name == "?") {
      EXPECT_EQ(faults & (1u << i), 0u)
          << "active fault bit " << i << " has no fault code";
      continue;
    }
    const std::string line = LineNaming(out, name);
    const bool active = (faults & (1u << i)) != 0;
    EXPECT_EQ(!line.empty(), active) << name << "\n" << out;
    if (active) {
      EXPECT_EQ(Contains(line, "(torque cut)"),
                (vcu::kTorqueCutFaults & (1u << i)) != 0)
          << line;
    }
  }
}

TEST_F(ShellCommandsTest, FaultsListsEachActiveFault) {
  ReachRtdWait();
  ExpectFaultListMatches(app_->faults(), Shell("/faults"));

  // One fault that cuts torque and one that doesn't.
  b_.inverter_alive = false;
  b_.dui_alive = false;
  b_.Run(*app_, 2000);
  const uint32_t faults = app_->faults();
  ASSERT_NE(faults & vcu::kFaultInverterTimeout, 0u);
  ASSERT_NE(faults & vcu::kFaultDuiTimeout, 0u);
  const std::string out = Shell("/faults");
  ExpectFaultListMatches(faults, out);
  EXPECT_TRUE(Contains(
      LineNaming(out, lhre::can::ToString(VcuFaultCode::kInverterTimeout)),
      "(torque cut)"));
  EXPECT_FALSE(
      Contains(LineNaming(out, lhre::can::ToString(VcuFaultCode::kDuiTimeout)),
               "(torque cut)"));
}

// "update." on the shell port reboots into the bootloader exactly once,
// whether the shell's line handler or the byte watcher sees it first.
TEST_F(ShellCommandsTest, UpdateLineEntersTheBootloaderOnce) {
  b_.Run(*app_, 30);
  Shell("update.");
  b_.Run(*app_, 25);
  EXPECT_EQ(b_.board.bootloader_requests, 1);
}

TEST_F(ShellCommandsTest, UnknownCommandIsReportedNotIgnored) {
  b_.Run(*app_, 30);
  EXPECT_TRUE(Contains(Shell("/mdoe 5"), "unknown command"));
  b_.Run(*app_, 10);
  EXPECT_EQ(app_->snapshot().event_mode, EventMode::kEndurance);
}

}  // namespace
