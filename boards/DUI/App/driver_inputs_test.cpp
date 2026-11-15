// Host tests for the driver-input debouncer, against LHAL GPIO fakes.

#include "driver_inputs.hpp"

#include <gtest/gtest.h>

#include "lhal/host/gpio.hpp"

namespace {

using dui::DriverInputs;
using dui::Input;
using dui::InputBit;

struct Board {
  lhal::host::Gpio lines[dui::kInputCount];
  lhal::Gpio* pins[dui::kInputCount] = {};
  Board() {
    for (size_t i = 0; i < dui::kInputCount; ++i) {
      pins[i] = &lines[i];
    }
  }
  lhal::host::Gpio& line(Input input) {
    return lines[static_cast<size_t>(input)];
  }
};

// Samples every millisecond over [from_ms, to_ms] and ORs the change masks.
uint16_t SampleRange(DriverInputs& in, uint32_t from_ms, uint32_t to_ms) {
  uint16_t changed = 0;
  for (uint32_t t = from_ms; t <= to_ms; ++t) {
    changed = static_cast<uint16_t>(changed | in.Sample(t));
  }
  return changed;
}

TEST(DriverInputs, StartsReleased) {
  Board board;
  DriverInputs in(board.pins);
  EXPECT_EQ(in.Sample(0), 0);
  EXPECT_EQ(in.state(), 0);
}

TEST(DriverInputs, PressAcceptedOnlyAfterMoreThanDebounceTime) {
  Board board;
  DriverInputs in(board.pins);
  in.Sample(0);

  board.line(Input::kButton1).Write(true);  // pressed at t = 10
  EXPECT_EQ(SampleRange(in, 10, 15), 0);    // held 5 ms: not yet
  EXPECT_FALSE(in.pressed(Input::kButton1));
  EXPECT_EQ(in.Sample(16), InputBit(Input::kButton1));  // > 5 ms: accepted
  EXPECT_TRUE(in.pressed(Input::kButton1));
  EXPECT_EQ(in.Sample(17), 0);  // reported once, not every sample
}

TEST(DriverInputs, ReleaseIsDebouncedToo) {
  Board board;
  DriverInputs in(board.pins);
  board.line(Input::kButton2).Write(true);
  SampleRange(in, 0, 10);
  ASSERT_TRUE(in.pressed(Input::kButton2));

  board.line(Input::kButton2).Write(false);
  EXPECT_EQ(SampleRange(in, 20, 25), 0);
  EXPECT_EQ(in.Sample(26), InputBit(Input::kButton2));
  EXPECT_FALSE(in.pressed(Input::kButton2));
}

TEST(DriverInputs, BounceRestartsTheWindow) {
  Board board;
  DriverInputs in(board.pins);
  in.Sample(0);

  auto& line = board.line(Input::kTopLeftUp);
  // Contact chatter: high 3 ms, low 1 ms, high again from t = 14. Each edge
  // restarts the window, so the press is only accepted at t = 14 + 6.
  line.Write(true);
  SampleRange(in, 10, 12);
  line.Write(false);
  SampleRange(in, 13, 13);
  line.Write(true);
  EXPECT_EQ(SampleRange(in, 14, 19), 0);
  EXPECT_EQ(in.Sample(20), InputBit(Input::kTopLeftUp));
}

TEST(DriverInputs, GlitchShorterThanDebounceIsIgnored) {
  Board board;
  DriverInputs in(board.pins);
  in.Sample(0);

  board.line(Input::kBottomRightDown).Write(true);
  SampleRange(in, 10, 13);  // 4 ms spike
  board.line(Input::kBottomRightDown).Write(false);
  EXPECT_EQ(SampleRange(in, 14, 40), 0);
  EXPECT_EQ(in.state(), 0);
}

TEST(DriverInputs, LinesAreIndependent) {
  Board board;
  DriverInputs in(board.pins);
  in.Sample(0);

  board.line(Input::kButton1).Write(true);
  SampleRange(in, 1, 3);
  board.line(Input::kTopRightDown).Write(true);  // starts 3 ms later
  EXPECT_EQ(SampleRange(in, 4, 7), InputBit(Input::kButton1));
  EXPECT_EQ(SampleRange(in, 8, 10), InputBit(Input::kTopRightDown));
  EXPECT_EQ(in.state(),
            InputBit(Input::kButton1) | InputBit(Input::kTopRightDown));
}

TEST(DriverInputs, MissingPinsReadReleased) {
  lhal::host::Gpio button;
  button.Write(true);
  lhal::Gpio* pins[dui::kInputCount] = {&button};  // only BTN1 wired
  DriverInputs in(pins);
  SampleRange(in, 0, 10);
  EXPECT_EQ(in.state(), InputBit(Input::kButton1));
}

TEST(DriverInputs, SurvivesClockWrap) {
  Board board;
  DriverInputs in(board.pins);
  const uint32_t near_wrap = 0xFFFFFFFFu - 2;
  in.Sample(near_wrap);
  board.line(Input::kButton1).Write(true);
  uint16_t changed = 0;
  for (uint32_t i = 0; i <= 6; ++i) {
    changed = static_cast<uint16_t>(changed | in.Sample(near_wrap + 1 + i));
  }
  EXPECT_EQ(changed, InputBit(Input::kButton1));
}

TEST(DriverInputs, NamesEveryInput) {
  for (size_t i = 0; i < dui::kInputCount; ++i) {
    EXPECT_STRNE(dui::InputName(static_cast<Input>(i)), "?");
  }
}

}  // namespace
