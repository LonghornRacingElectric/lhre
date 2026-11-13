// Tests for CanInbox (ISR-to-control frame queue) and RxSlot (latest copy of
// one message, with its timeout), run against the LHAL host CAN backend.

#include "longhorn/can_inbox.hpp"

#include <cstdint>
#include <cstring>

#include <gtest/gtest.h>

#include "lhal/host/can.hpp"

namespace {

using longhorn::CanInbox;

lhal::CanFrame Frame(uint32_t id, uint8_t len, uint8_t fill = 0) {
  lhal::CanFrame f;
  f.id = id;
  f.len = len;
  for (uint8_t i = 0; i < len && i < lhal::CanFrame::kMaxData; ++i) {
    f.data[i] = static_cast<uint8_t>(fill + i);
  }
  return f;
}

class CanInboxTest : public ::testing::Test {
 protected:
  void SetUp() override { inbox_.Attach(&bus_); }

  lhal::host::Can bus_;
  CanInbox inbox_;
};

TEST_F(CanInboxTest, EmptyInboxPopsNothing) {
  lhal::CanFrame out;
  EXPECT_FALSE(inbox_.Pop(&out));
  EXPECT_EQ(inbox_.dropped(), 0u);
}

TEST_F(CanInboxTest, FramesComeOutInArrivalOrderWithTheirFields) {
  lhal::CanFrame ext = Frame(0x1ABCDEF0, 3, 0x40);
  ext.extended_id = true;
  bus_.Inject(Frame(0x100, 8, 0x10));
  bus_.Inject(ext);
  bus_.Inject(Frame(0x7FF, 0));

  lhal::CanFrame out;
  ASSERT_TRUE(inbox_.Pop(&out));
  EXPECT_EQ(out.id, 0x100u);
  EXPECT_FALSE(out.extended_id);
  ASSERT_EQ(out.len, 8);
  for (uint8_t i = 0; i < 8; ++i) EXPECT_EQ(out.data[i], 0x10 + i);

  ASSERT_TRUE(inbox_.Pop(&out));
  EXPECT_EQ(out.id, 0x1ABCDEF0u);
  EXPECT_TRUE(out.extended_id);
  EXPECT_EQ(out.len, 3);
  EXPECT_EQ(out.data[2], 0x42);

  ASSERT_TRUE(inbox_.Pop(&out));
  EXPECT_EQ(out.id, 0x7FFu);
  EXPECT_EQ(out.len, 0);
  EXPECT_FALSE(inbox_.Pop(&out));
}

// A decoder must never see the previous frame's bytes past `len`.
TEST_F(CanInboxTest, PoppedFrameCarriesNoStaleBytes) {
  bus_.Inject(Frame(0x200, 2, 0xA0));
  lhal::CanFrame out;
  std::memset(out.data, 0xEE, sizeof(out.data));
  out.fd = true;
  ASSERT_TRUE(inbox_.Pop(&out));
  EXPECT_FALSE(out.fd);
  for (size_t i = 2; i < lhal::CanFrame::kMaxData; ++i) {
    EXPECT_EQ(out.data[i], 0) << "byte " << i;
  }
}

TEST_F(CanInboxTest, FrameLongerThanClassicCanIsDroppedAndCounted) {
  lhal::CanFrame fd = Frame(0x300, 12);
  fd.fd = true;
  bus_.Inject(fd);
  lhal::CanFrame out;
  EXPECT_FALSE(inbox_.Pop(&out));
  EXPECT_EQ(inbox_.dropped(), 1u);
}

TEST_F(CanInboxTest, FullInboxDropsTheNewestFrameAndRecovers) {
  for (uint32_t i = 0; i < CanInbox::kDepth; ++i) bus_.Inject(Frame(i, 1));
  bus_.Inject(Frame(0x999, 1));
  EXPECT_EQ(inbox_.dropped(), 1u);

  lhal::CanFrame out;
  ASSERT_TRUE(inbox_.Pop(&out));
  EXPECT_EQ(out.id, 0u);  // the oldest frames are kept, not overwritten
  bus_.Inject(Frame(0x555, 1));
  EXPECT_EQ(inbox_.dropped(), 1u);

  uint32_t last = 0;
  int count = 0;
  while (inbox_.Pop(&out)) {
    last = out.id;
    ++count;
  }
  EXPECT_EQ(count, static_cast<int>(CanInbox::kDepth));
  EXPECT_EQ(last, 0x555u);
}

TEST_F(CanInboxTest, OrderHoldsAcrossManyWrapsOfTheRing) {
  lhal::CanFrame out;
  uint32_t next_in = 0;
  uint32_t next_out = 0;
  for (int round = 0; round < 10; ++round) {
    for (int i = 0; i < 37; ++i) bus_.Inject(Frame(next_in++, 1));
    while (inbox_.Pop(&out)) {
      ASSERT_EQ(out.id, next_out);
      ++next_out;
    }
  }
  EXPECT_EQ(next_out, next_in);
  EXPECT_EQ(inbox_.dropped(), 0u);
}

TEST(CanInbox, AttachingNoBusIsHarmless) {
  CanInbox inbox;
  inbox.Attach(nullptr);
  lhal::CanFrame out;
  EXPECT_FALSE(inbox.Pop(&out));
}

// A stand-in for a generated CAN message.
template <int kHz100>
struct FakeMessage {
  static constexpr float kFrequencyHz = kHz100 / 100.0f;
  static constexpr uint8_t kDlc = 2;
  static bool Matches(uint32_t id) { return id == 0x123; }
  static FakeMessage FromFrame(const lhal::CanFrame& f) {
    FakeMessage m;
    m.value = static_cast<uint16_t>(f.data[0] | (f.data[1] << 8));
    return m;
  }
  uint16_t value = 0;
};

using Periodic100Hz = FakeMessage<10000>;
using Periodic3Hz = FakeMessage<300>;
using Periodic6Hz = FakeMessage<600>;
using Aperiodic = FakeMessage<0>;

TEST(RxSlot, TimeoutIsTwiceThePeriodRoundedUp) {
  EXPECT_EQ(longhorn::RxSlot<Periodic100Hz>::kTimeoutMs, 20u);
  EXPECT_EQ(longhorn::RxSlot<Periodic3Hz>::kTimeoutMs, 667u);
  EXPECT_EQ(longhorn::RxSlot<Periodic6Hz>::kTimeoutMs,
            334u);  // up, not nearest
  EXPECT_EQ(longhorn::RxSlot<Aperiodic>::kTimeoutMs, 0u);
}

TEST(RxSlot, DecodesOnlyItsOwnStandardFullLengthFrame) {
  longhorn::RxSlot<Periodic100Hz> slot;
  lhal::CanFrame good = Frame(0x123, 2);
  good.data[0] = 0x34;
  good.data[1] = 0x12;

  lhal::CanFrame other_id = good;
  other_id.id = 0x124;
  lhal::CanFrame extended = good;
  extended.extended_id = true;  // same number, different identifier space
  lhal::CanFrame short_frame = good;
  short_frame.len = 1;

  EXPECT_FALSE(slot.TryDecode(other_id, 5));
  EXPECT_FALSE(slot.TryDecode(extended, 5));
  EXPECT_FALSE(slot.TryDecode(short_frame, 5));
  EXPECT_FALSE(slot.seen());

  EXPECT_TRUE(slot.TryDecode(good, 5));
  EXPECT_TRUE(slot.seen());
  EXPECT_EQ(slot.msg.value, 0x1234);
  EXPECT_EQ(slot.last_ms, 5u);
  EXPECT_EQ(slot.count, 1u);
}

TEST(RxSlot, FreshUntilTheTimeoutAndNotAtIt) {
  longhorn::RxSlot<Periodic100Hz> slot;
  EXPECT_FALSE(slot.Fresh(0));  // never received is never fresh
  ASSERT_TRUE(slot.TryDecode(Frame(0x123, 2), 1000));
  EXPECT_TRUE(slot.Fresh(1000));
  EXPECT_TRUE(slot.Fresh(1019));
  EXPECT_FALSE(slot.Fresh(1020));

  ASSERT_TRUE(slot.TryDecode(Frame(0x123, 2), 1030));  // a new frame revives it
  EXPECT_TRUE(slot.Fresh(1040));
  EXPECT_EQ(slot.count, 2u);
}

TEST(RxSlot, FreshnessSurvivesTheMillisecondCounterWrap) {
  longhorn::RxSlot<Periodic100Hz> slot;
  ASSERT_TRUE(slot.TryDecode(Frame(0x123, 2), UINT32_MAX - 5));
  EXPECT_TRUE(slot.Fresh(10));   // 16 ms later
  EXPECT_FALSE(slot.Fresh(14));  // 20 ms later
}

TEST(RxSlot, AperiodicMessageIsNeverFreshByAge) {
  longhorn::RxSlot<Aperiodic> slot;
  ASSERT_TRUE(slot.TryDecode(Frame(0x123, 2), 100));
  EXPECT_TRUE(slot.seen());
  EXPECT_FALSE(slot.Fresh(100));
}

}  // namespace
