// What the VCU puts on the Data bus (app/telemetry.cpp): diagnostics at
// their spec rates, what they carry, and the brake-latch flash write.

#include <gtest/gtest.h>

#include <memory>

#include "app/vcu_app.hpp"
#include "lhre_can_vcu.hpp"
#include "safety/faults.hpp"
#include "test/bench.hpp"

namespace {

using vcu::bench::Bench;
namespace msg = lhre::can::vcu;

template <class M>
int Count(const lhal::host::Can& bus) {
  int n = 0;
  for (const lhal::CanFrame& f : bus.sent()) n += M::Matches(f.id) ? 1 : 0;
  return n;
}

// The telemetry tick is 10 ms, so 100 Hz and 10 Hz frames come out exact
// up to the window's edges.
template <class M>
void ExpectSpecRate(const lhal::host::Can& bus, uint32_t ms) {
  const float expected = M::kFrequencyHz * static_cast<float>(ms) / 1000.0f;
  EXPECT_NEAR(static_cast<float>(Count<M>(bus)), expected,
              0.01f * expected + 1.0f)
      << "frame id 0x" << std::hex << M::kFrameId;
}

TEST(Telemetry, EveryDiagnosticGoesOutAtItsSpecRate) {
  Bench b;
  const auto app = std::make_unique<vcu::App>(b.peripherals());
  b.Run(*app, 500);
  b.vcu_data.ClearSent();
  constexpr uint32_t kMs = 3000;
  b.Run(*app, kMs);

  ExpectSpecRate<msg::VcuAdcCountsA>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuAdcCountsB>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuAdcVoltsA>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuAdcVoltsB>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuCalibratedA>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuCalibratedB>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuDigitalIo>(b.vcu_data, kMs);
  ExpectSpecRate<msg::VcuDiag>(b.vcu_data, kMs);

  // Nothing else: control traffic belongs on the Critical bus.
  const int known = Count<msg::VcuAdcCountsA>(b.vcu_data) +
                    Count<msg::VcuAdcCountsB>(b.vcu_data) +
                    Count<msg::VcuAdcVoltsA>(b.vcu_data) +
                    Count<msg::VcuAdcVoltsB>(b.vcu_data) +
                    Count<msg::VcuCalibratedA>(b.vcu_data) +
                    Count<msg::VcuCalibratedB>(b.vcu_data) +
                    Count<msg::VcuDigitalIo>(b.vcu_data) +
                    Count<msg::VcuDiag>(b.vcu_data);
  EXPECT_EQ(static_cast<size_t>(known), b.vcu_data.sent().size());
}

TEST(Telemetry, HealthCarriesBootCauseUptimeAndBothBusesStats) {
  Bench b;
  vcu::Peripherals p = b.peripherals();
  p.reset_cause = lhal::ResetCause::kIndependentWatchdog;
  const auto app = std::make_unique<vcu::App>(p);
  lhal::CanStats critical;
  critical.bus_off_count = 2;
  critical.tx_dropped = 7;
  b.vcu_critical.set_stats(critical);
  lhal::CanStats data;
  data.bus_off_count = 1;
  data.tx_dropped = 300;  // saturates
  b.vcu_data.set_stats(data);
  b.Run(*app, 2550);

  msg::VcuDiag d;
  ASSERT_TRUE(b.LastFrame(b.logger, &d));
  EXPECT_EQ(d.reset_cause, lhre::can::ResetCause::kIndependentWatchdog);
  EXPECT_EQ(d.uptime, 2);
  EXPECT_EQ(d.critical_bus_off_count, 2);
  EXPECT_EQ(d.critical_tx_dropped, 7);
  EXPECT_EQ(d.data_bus_off_count, 1);
  EXPECT_EQ(d.data_tx_dropped, 255);
}

// The calibrated frames exist to show a sensor past its calibrated span,
// so they carry the values before the pedal logic clamps them.
TEST(Telemetry, CalibratedFramesAreNotClamped) {
  Bench b;
  const auto app = std::make_unique<vcu::App>(b.peripherals());
  b.SetPedalTravel(1.1f);
  b.Run(*app, 100);
  msg::VcuCalibratedA ka;
  ASSERT_TRUE(b.LastFrame(b.logger, &ka));
  EXPECT_NEAR(ka.apps1_travel(), 1.1f, 0.01f);
  EXPECT_NEAR(ka.apps2_travel(), 1.1f, 0.01f);

  b.SetPedalTravel(-0.1f);
  b.Run(*app, 100);
  ASSERT_TRUE(b.LastFrame(b.logger, &ka));
  EXPECT_NEAR(ka.apps1_travel(), -0.1f, 0.01f);
  EXPECT_NEAR(ka.apps2_travel(), -0.1f, 0.01f);
}

TEST(Telemetry, DigitalFrameShowsTheRtdButton) {
  Bench b;
  const auto app = std::make_unique<vcu::App>(b.peripherals());
  b.Run(*app, 100);
  msg::VcuDigitalIo io;
  ASSERT_TRUE(b.LastFrame(b.logger, &io));
  EXPECT_FALSE(io.inputs_rtd_switch);
  b.PressRtd(true);
  b.Run(*app, 100);
  ASSERT_TRUE(b.LastFrame(b.logger, &io));
  EXPECT_TRUE(io.inputs_rtd_switch);
}

// The latch is written to flash once per change, from the telemetry task,
// not on every tick while it stays set.
TEST(Telemetry, BrakeLatchIsSavedOncePerChange) {
  Bench b;
  const auto app = std::make_unique<vcu::App>(b.peripherals());
  b.Run(*app, 100);
  EXPECT_EQ(b.board.saves, 0);

  b.b1_disconnect.Write(true);
  b.Run(*app, 30);
  b.b1_disconnect.Write(false);
  b.Run(*app, 2000);
  ASSERT_NE(app->faults() & vcu::kFaultBrakeFlagLatched, 0u);
  EXPECT_EQ(b.board.saves, 1);
  EXPECT_EQ(b.board.stored, vcu::BrakeFlagLatch::kPersistedSet);

  b.fault_reset = true;
  b.Run(*app, 2000);
  EXPECT_EQ(app->faults() & vcu::kFaultBrakeFlagLatched, 0u);
  EXPECT_EQ(b.board.saves, 2);
  EXPECT_EQ(b.board.stored, 0u);
}

TEST(Telemetry, NoDataBusKeepsTheWatchdogFed) {
  Bench b;
  vcu::Peripherals p = b.peripherals();
  p.data_can = nullptr;
  const auto app = std::make_unique<vcu::App>(p);
  b.Run(*app, 500);
  const uint32_t refreshes = app->watchdog_refreshes();
  b.Run(*app, 500);
  // A missing Data bus must not read as a stalled telemetry task.
  EXPECT_GT(app->watchdog_refreshes(), refreshes);
  EXPECT_TRUE(b.vcu_data.sent().empty());
  EXPECT_GT(b.vcu_critical.sent().size(), 0u);
}

}  // namespace
