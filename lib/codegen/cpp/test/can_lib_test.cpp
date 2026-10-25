// Host tests for the generated CAN library. Byte patterns are computed
// by hand from the seed spec, so they pin the wire format independently
// of the generator's own bit logic.

#include <cstring>

#include <gtest/gtest.h>

#include "lhre_can.hpp"
#include "lhre_can_rx_pi.hpp"
#include "lhre_can_rx_vcu.hpp"

namespace lhre::can {
namespace {

// Messages are generated into one namespace per source board.
using hvc::HvcCellTemps;
using hvc::HvcPackStatus;
using vcu::VcuBootloaderData;
using vcu::VcuDebug;
using vcu::VcuStatus;

TEST(CanLib, VcuStatusPackMatchesHandComputedBytes) {
  VcuStatus msg;
  msg.state = VcuState::kDrive;    // byte 0 = 0x02
  msg.faults_overtemp = true;      // bit 8
  msg.faults_sensor_loss = true;   // bit 10 -> byte 1 = 0x05
  msg.torque_request_raw = -1234;  // 0xFB2E little-endian
  msg.speed_raw = 0x1234;
  msg.steering_angle_raw = -100;  // 0xFF9C big-endian at bit 55

  uint8_t data[VcuStatus::kDlc];
  msg.Pack(data);

  const uint8_t expected[8] = {0x02, 0x05, 0x2E, 0xFB, 0x34, 0x12, 0xFF, 0x9C};
  EXPECT_EQ(0, std::memcmp(data, expected, sizeof(expected)));
}

TEST(CanLib, VcuStatusRoundTripsThroughLhalFrame) {
  VcuStatus msg;
  msg.state = VcuState::kFault;
  msg.faults_overcurrent = true;
  msg.torque_request_raw = -3000;
  msg.speed_raw = 65535;
  msg.steering_angle_raw = -1800;

  lhal::CanFrame frame = msg.ToFrame();
  EXPECT_EQ(frame.id, VcuStatus::kFrameId);
  EXPECT_EQ(frame.len, VcuStatus::kDlc);
  ASSERT_TRUE(VcuStatus::Matches(frame.id));

  VcuStatus out = VcuStatus::FromFrame(frame);
  EXPECT_EQ(out.state, VcuState::kFault);
  EXPECT_FALSE(out.faults_overtemp);
  EXPECT_TRUE(out.faults_overcurrent);
  EXPECT_FALSE(out.faults_sensor_loss);
  EXPECT_EQ(out.torque_request_raw, -3000);
  EXPECT_EQ(out.speed_raw, 65535);
  EXPECT_EQ(out.steering_angle_raw, -1800);
}

TEST(CanLib, HvcPackStatusRoundTrip) {
  HvcPackStatus msg;
  msg.pack_voltage_raw = 5432;  // 543.2 V
  msg.pack_current_raw = -875;  // -87.5 A
  msg.soc_raw = 191;
  msg.coolant_temp = -40;  // scale 1 -> raw field keeps the bare name

  uint8_t data[HvcPackStatus::kDlc];
  msg.Pack(data);
  HvcPackStatus out;
  out.Unpack(data);

  EXPECT_EQ(out.pack_voltage_raw, 5432);
  EXPECT_EQ(out.pack_current_raw, -875);
  EXPECT_EQ(out.soc_raw, 191);
  EXPECT_EQ(out.coolant_temp, -40);
}

TEST(CanLib, MuxPacksOnlyTheSelectedChannel) {
  VcuDebug msg;
  msg.channel = 1;
  msg.loop_time = 0xDEADBEEF;  // channel 0 signal; must NOT be packed
  msg.cpu_load_raw = 5000;     // 50.00 %

  uint8_t data[VcuDebug::kDlc];
  msg.Pack(data);

  const uint8_t expected[8] = {0x01, 0x88, 0x13, 0x00, 0x00, 0x00, 0x00, 0x00};
  EXPECT_EQ(0, std::memcmp(data, expected, sizeof(expected)));

  VcuDebug out;
  out.loop_time = 111;  // must survive unpacking a channel-1 frame
  out.Unpack(data);
  EXPECT_EQ(out.channel, 1);
  EXPECT_EQ(out.cpu_load_raw, 5000);
  EXPECT_EQ(out.loop_time, 111u);
}

TEST(CanLib, MuxChannelZero) {
  VcuDebug msg;
  msg.channel = 0;
  msg.loop_time = 0xDEADBEEF;
  msg.cpu_load_raw = 1234;  // channel 1 signal; must not be packed

  uint8_t data[VcuDebug::kDlc];
  msg.Pack(data);
  VcuDebug out;
  out.Unpack(data);
  EXPECT_EQ(out.loop_time, 0xDEADBEEFu);
  EXPECT_EQ(out.cpu_load_raw, 0);
}

TEST(CanLib, FloatAccessorsScaleAndRound) {
  VcuStatus msg;
  msg.torque_request_raw = -1234;
  EXPECT_NEAR(msg.torque_request(), -123.4f, 1e-3f);
  msg.set_torque_request(-123.4f);
  EXPECT_EQ(msg.torque_request_raw, -1234);
  // Round-to-nearest, both signs.
  msg.set_torque_request(0.06f);
  EXPECT_EQ(msg.torque_request_raw, 1);
  msg.set_torque_request(-0.06f);
  EXPECT_EQ(msg.torque_request_raw, -1);

  HvcPackStatus hvc;
  hvc.soc_raw = 191;
  EXPECT_NEAR(hvc.soc(), 95.5f, 1e-3f);
}

TEST(CanLib, EnumToString) {
  EXPECT_STREQ(ToString(VcuState::kDrive), "DRIVE");
  EXPECT_STREQ(ToString(static_cast<VcuState>(200)), "?");
}

// First metadata entry for `frame_id`, or nullptr. (The same ID may appear
// once per bus.)
const MessageMeta* FindMeta(uint32_t frame_id) {
  for (size_t i = 0; i < kMessageCount; ++i) {
    if (kMessageMeta[i].frame_id == frame_id) {
      return &kMessageMeta[i];
    }
  }
  return nullptr;
}

TEST(CanLib, QuantityBlockAndMetadata) {
  ASSERT_GT(kMessageCount, 0u);
  // Sorted by frame ID.
  for (size_t i = 1; i < kMessageCount; ++i) {
    EXPECT_LE(kMessageMeta[i - 1].frame_id, kMessageMeta[i].frame_id) << i;
  }
  // The bootloader block occupies 0x010-0x013.
  const MessageMeta* boot_meta = FindMeta(VcuBootloaderData::kFrameId);
  ASSERT_NE(boot_meta, nullptr);
  EXPECT_EQ(boot_meta->quantity, 4);
  EXPECT_EQ(boot_meta->frequency_hz, 0.0f);
  const MessageMeta* status_meta = FindMeta(0x300u);
  ASSERT_NE(status_meta, nullptr);
  EXPECT_EQ(status_meta->dlc, 8);
  const MessageMeta* pack_meta = FindMeta(0x400u);
  ASSERT_NE(pack_meta, nullptr);
  EXPECT_EQ(pack_meta->frequency_hz, 50.0f);

  EXPECT_TRUE(VcuBootloaderData::Matches(0x013));
  EXPECT_FALSE(VcuBootloaderData::Matches(0x014));
  VcuBootloaderData boot;
  lhal::CanFrame frame = boot.ToFrame(3);
  EXPECT_EQ(frame.id, 0x013u);
}

// Indexed array block: 4 temp slots per frame over 4 consecutive IDs;
// cell = (frame_id - kFrameId) * 4 + slot.
TEST(CanLib, CellTempArrayBlock) {
  HvcCellTemps msg;
  msg.set_cell_temp_slot0(21.5f);
  msg.cell_temp_slot1_raw = -113;  // -11.3 degC
  msg.set_cell_temp_slot2(0.0f);
  msg.set_cell_temp_slot3(45.9f);

  lhal::CanFrame frame = msg.ToFrame(2);  // frame for cells 8-11
  EXPECT_EQ(frame.id, HvcCellTemps::kFrameId + 2);
  ASSERT_TRUE(HvcCellTemps::Matches(frame.id));
  EXPECT_FALSE(HvcCellTemps::Matches(HvcCellTemps::kFrameId + 4));

  HvcCellTemps out = HvcCellTemps::FromFrame(frame);
  EXPECT_NEAR(out.cell_temp_slot0(), 21.5f, 1e-3f);
  EXPECT_EQ(out.cell_temp_slot1_raw, -113);
  EXPECT_EQ(out.cell_temp_slot2_raw, 0);
  EXPECT_NEAR(out.cell_temp_slot3(), 45.9f, 1e-3f);
}

}  // namespace
}  // namespace lhre::can

TEST(CanRx, DispatchChecksFrameAndPreservesArrivalTime) {
  lhre::can::rx::VcuCriticalRx rx;
  lhre::can::inverter::InverterCurrent message;
  message.set_dc_bus_current(12.5f);
  auto frame = message.ToFrame();
  frame.extended_id = true;
  EXPECT_FALSE(rx.Dispatch(frame, 10));
  frame.extended_id = false;
  --frame.len;
  EXPECT_FALSE(rx.Dispatch(frame, 20));
  ++frame.len;
  EXPECT_TRUE(rx.Dispatch(frame, 30));
  EXPECT_EQ(rx.inverter_current.count, 1u);
  EXPECT_EQ(rx.inverter_current.last_ms, 30u);
  EXPECT_FLOAT_EQ(rx.inverter_current.msg.dc_bus_current(), 12.5f);
  EXPECT_TRUE(rx.inverter_current.Fresh(229));
  EXPECT_FALSE(rx.inverter_current.Fresh(230));
  lhre::can::rx::VcuDataRx data;
  EXPECT_FALSE(data.Dispatch(frame, 30));
}

TEST(CanRx, MixedCaseRecipientAndAperiodicMessage) {
  lhre::can::rx::PiDataRx pi;
  lhre::can::rx::VcuDataRx vcu;
  lhre::can::usm::AccelerationVectorUnsprungWheelSpeedFl wheel;
  EXPECT_TRUE(pi.Dispatch(wheel.ToFrame(), 0));
  EXPECT_TRUE(vcu.Dispatch(wheel.ToFrame(), 0));
  lhre::can::rx::VcuCriticalRx critical;
  lhre::can::pi::VcuModeCommand mode;
  EXPECT_TRUE(critical.Dispatch(mode.ToFrame(), 100));
  EXPECT_TRUE(critical.vcu_mode_command.seen());
  EXPECT_FALSE(critical.vcu_mode_command.Fresh(100));
}
