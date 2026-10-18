// Host tests for the generated CAN library. Byte patterns are computed
// independently from the lhre-2026 CSV layout so they pin the migrated wire
// format rather than the generator's implementation.

#include <cstring>

#include <gtest/gtest.h>

#include "lhre_can.hpp"

namespace lhre::can {
namespace {

using hvc::BatteryPackStatus;
using hvc::CellTemperatures;
using hvc::CellVoltages;
using hvc::ContactorStatus;
using vcu::VcuFuses;
using vcu::VcuState;

TEST(CanLib, VcuStatePackMatchesLhre2026Layout) {
  VcuState msg;
  msg.prndl_state = 2;
  msg.stomp_fault = true;
  msg.ready_to_drive_buzzer = false;
  msg.state_of_charge_estimate_raw = 955;
  msg.line_lock_enabled = true;
  msg.event_mode = 4;

  uint8_t data[VcuState::kDlc];
  msg.Pack(data);

  const uint8_t expected[7] = {0x02, 0x01, 0x00, 0xBB, 0x03, 0x01, 0x04};
  EXPECT_EQ(0, std::memcmp(data, expected, sizeof(expected)));
}

TEST(CanLib, VcuStateRoundTripsThroughLhalFrame) {
  VcuState msg;
  msg.prndl_state = 3;
  msg.stomp_fault = true;
  msg.ready_to_drive_buzzer = true;
  msg.state_of_charge_estimate_raw = 500;
  msg.line_lock_enabled = true;
  msg.event_mode = 7;

  lhal::CanFrame frame = msg.ToFrame();
  EXPECT_EQ(frame.id, VcuState::kFrameId);
  EXPECT_EQ(frame.len, VcuState::kDlc);
  ASSERT_TRUE(VcuState::Matches(frame.id));

  VcuState out = VcuState::FromFrame(frame);
  EXPECT_EQ(out.prndl_state, 3);
  EXPECT_TRUE(out.stomp_fault);
  EXPECT_TRUE(out.ready_to_drive_buzzer);
  EXPECT_EQ(out.state_of_charge_estimate_raw, 500);
  EXPECT_TRUE(out.line_lock_enabled);
  EXPECT_EQ(out.event_mode, 7);
}

TEST(CanLib, BatteryPackStatusRoundTrip) {
  BatteryPackStatus msg;
  msg.pack_voltage_raw = 54320;
  msg.tractive_current_raw = 8750;
  msg.state_of_charge_raw = 9550;
  msg.cell_top_temp = 41;
  msg.cell_bottom_temp = 37;

  uint8_t data[BatteryPackStatus::kDlc];
  msg.Pack(data);
  BatteryPackStatus out;
  out.Unpack(data);

  EXPECT_NEAR(out.pack_voltage(), 543.2f, 1e-3f);
  EXPECT_NEAR(out.tractive_current(), 87.5f, 1e-3f);
  EXPECT_NEAR(out.state_of_charge(), 95.5f, 1e-3f);
  EXPECT_EQ(out.cell_top_temp, 41);
  EXPECT_EQ(out.cell_bottom_temp, 37);
}

TEST(CanLib, ImportedBitfieldsPackByCsvBitIndex) {
  VcuFuses msg;
  msg.vcu_fuses_1_batt_pump_fuse = true;
  msg.vcu_fuses_1_batt_fans_fuse = true;
  msg.vcu_fuses_1_boards_fuse = true;
  msg.vcu_fuses_2_brake_light_fuse = true;
  msg.vcu_fuses_2_spare_fuse = true;

  uint8_t data[VcuFuses::kDlc];
  msg.Pack(data);

  const uint8_t expected[2] = {0x89, 0x05};
  EXPECT_EQ(0, std::memcmp(data, expected, sizeof(expected)));
}

TEST(CanLib, FloatAccessorsScaleAndRound) {
  VcuState state;
  state.set_state_of_charge_estimate(95.5f);
  EXPECT_EQ(state.state_of_charge_estimate_raw, 955);
  EXPECT_NEAR(state.state_of_charge_estimate(), 95.5f, 1e-3f);

  CellVoltages cells;
  cells.set_voltage_i(4.1234f);
  EXPECT_EQ(cells.voltage_i_raw, 41234);
  EXPECT_NEAR(cells.voltage_i(), 4.1234f, 1e-4f);
}

TEST(CanLib, CompleteMatrixMetadataAndCorrectedContactorDlc) {
  EXPECT_EQ(kMessageCount, 71u);
  EXPECT_EQ(ContactorStatus::kFrameId, 0x131u);
  EXPECT_EQ(ContactorStatus::kDlc, 4u);
  EXPECT_EQ(VcuState::kFrameId, 0x1C7u);
  EXPECT_EQ(VcuState::kFrequencyHz, 333.0f);
}

TEST(CanLib, Lhre2026CellVoltageRange) {
  EXPECT_EQ(CellVoltages::kFrameId, 0x0D0u);
  EXPECT_EQ(CellVoltages::kQuantity, 33u);
  EXPECT_TRUE(CellVoltages::Matches(0x0F0));
  EXPECT_FALSE(CellVoltages::Matches(0x0F1));

  CellVoltages cells;
  lhal::CanFrame frame = cells.ToFrame(32);
  EXPECT_EQ(frame.id, 0x0F0u);
}

TEST(CanLib, Lhre2026CellTemperatureRangeAndSlots) {
  EXPECT_EQ(CellTemperatures::kFrameId, 0x100u);
  EXPECT_EQ(CellTemperatures::kQuantity, 23u);

  CellTemperatures msg;
  msg.set_temp_i(21.5f);
  msg.temp_i_1_raw = 113;
  msg.set_temp_i_2(0.0f);
  msg.set_temp_i_3(45.9f);

  lhal::CanFrame frame = msg.ToFrame(22);
  EXPECT_EQ(frame.id, 0x116u);
  ASSERT_TRUE(CellTemperatures::Matches(frame.id));
  EXPECT_FALSE(CellTemperatures::Matches(0x117));

  CellTemperatures out = CellTemperatures::FromFrame(frame);
  EXPECT_NEAR(out.temp_i(), 21.5f, 1e-3f);
  EXPECT_EQ(out.temp_i_1_raw, 113);
  EXPECT_EQ(out.temp_i_2_raw, 0);
  EXPECT_NEAR(out.temp_i_3(), 45.9f, 1e-3f);
}

}  // namespace
}  // namespace lhre::can
