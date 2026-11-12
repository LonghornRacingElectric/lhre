// What the VCU puts on the Critical bus (app/critical_tx.cpp): which
// frames, how often, and the one-shot inverter fault clear.

#include <gtest/gtest.h>

#include <memory>

#include "app/vcu_app.hpp"
#include "lhre_can_vcu.hpp"
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

// Frames of M sent over `ms` match the spec rate. The control tick is
// 3 ms, so a 333 Hz frame really goes out at 333.3 Hz: allow 1%.
template <class M>
void ExpectSpecRate(const lhal::host::Can& bus, uint32_t ms) {
  const float expected = M::kFrequencyHz * static_cast<float>(ms) / 1000.0f;
  EXPECT_NEAR(static_cast<float>(Count<M>(bus)), expected,
              0.01f * expected + 1.0f)
      << "frame id 0x" << std::hex << M::kFrameId;
}

// Refuses the first `refusals` sends of one frame ID, like a full TX queue.
class RefusingBus final : public lhal::CanBus {
 public:
  RefusingBus(lhal::CanBus* inner, uint32_t id, int refusals)
      : inner_(inner), id_(id), refusals_(refusals) {}
  lhal::Status Send(const lhal::CanFrame& frame) override {
    if (frame.id == id_ && refusals_ > 0) {
      --refusals_;
      ++refused;
      return lhal::Status::kBusy;
    }
    return inner_->Send(frame);
  }
  bool Receive(lhal::CanFrame* out) override { return inner_->Receive(out); }
  void SetRxCallback(RxCallback cb, void* context) override {
    inner_->SetRxCallback(cb, context);
  }
  int refused = 0;

 private:
  lhal::CanBus* inner_;
  uint32_t id_;
  int refusals_;
};

class CriticalTxTest : public ::testing::Test {
 protected:
  Bench b_;
};

TEST_F(CriticalTxTest, EveryFrameGoesOutAtItsSpecRate) {
  const auto app = std::make_unique<vcu::App>(b_.peripherals());
  b_.Energize();
  b_.Run(*app, 1500);  // past the fault clear
  b_.vcu_critical.ClearSent();
  constexpr uint32_t kMs = 3000;
  b_.Run(*app, kMs);

  ExpectSpecRate<msg::InverterTorqueCommand>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::AppsVoltages>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::AcceleratorPedal>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::BrakePedal>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::BseVoltages>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::Brakes>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::SteeringColumn>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::VcuState>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::VcuFaults>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::EnergyEstimate>(b_.vcu_critical, kMs);
  ExpectSpecRate<msg::TorquePath>(b_.vcu_critical, kMs);

  // Nothing else: diagnostics belong on the Data bus.
  int known = 0;
  known += Count<msg::InverterTorqueCommand>(b_.vcu_critical);
  known += Count<msg::AppsVoltages>(b_.vcu_critical);
  known += Count<msg::AcceleratorPedal>(b_.vcu_critical);
  known += Count<msg::BrakePedal>(b_.vcu_critical);
  known += Count<msg::BseVoltages>(b_.vcu_critical);
  known += Count<msg::Brakes>(b_.vcu_critical);
  known += Count<msg::SteeringColumn>(b_.vcu_critical);
  known += Count<msg::VcuState>(b_.vcu_critical);
  known += Count<msg::VcuFaults>(b_.vcu_critical);
  known += Count<msg::EnergyEstimate>(b_.vcu_critical);
  known += Count<msg::TorquePath>(b_.vcu_critical);
  EXPECT_EQ(static_cast<size_t>(known), b_.vcu_critical.sent().size());
}

TEST_F(CriticalTxTest, FaultClearGoesOutOnceAfterTheBootDelay) {
  const auto app = std::make_unique<vcu::App>(b_.peripherals());
  const uint32_t delay = b_.params.inverter.fault_clear_delay_ms;
  // The inverter ignores a clear sent before it has booted.
  b_.Run(*app, delay - 3);
  EXPECT_EQ(Count<msg::InverterParameterRequest>(b_.vcu_critical), 0);
  b_.Run(*app, 6);
  ASSERT_EQ(Count<msg::InverterParameterRequest>(b_.vcu_critical), 1);
  b_.Run(*app, 5000);
  EXPECT_EQ(Count<msg::InverterParameterRequest>(b_.vcu_critical), 1);

  for (const lhal::CanFrame& f : b_.vcu_critical.sent()) {
    if (!msg::InverterParameterRequest::Matches(f.id)) continue;
    const auto clear = msg::InverterParameterRequest::FromFrame(f);
    EXPECT_EQ(clear.parameter_address, b_.params.inverter.fault_clear_address);
    EXPECT_TRUE(clear.r_w);  // write
    EXPECT_EQ(clear.data_to_be_written, 0u);
  }
}

TEST_F(CriticalTxTest, RefusedFaultClearIsRetriedUntilItGoesOut) {
  RefusingBus refusing(&b_.vcu_critical,
                       msg::InverterParameterRequest::kFrameId, 3);
  vcu::Peripherals p = b_.peripherals();
  p.critical_can = &refusing;
  const auto app = std::make_unique<vcu::App>(p);
  b_.Run(*app, b_.params.inverter.fault_clear_delay_ms + 30);
  EXPECT_EQ(refusing.refused, 3);
  EXPECT_EQ(Count<msg::InverterParameterRequest>(b_.vcu_critical), 1);
  b_.Run(*app, 1000);
  EXPECT_EQ(Count<msg::InverterParameterRequest>(b_.vcu_critical), 1);
}

TEST_F(CriticalTxTest, TorqueCommandStaysDisabledOutsideDrive) {
  const auto app = std::make_unique<vcu::App>(b_.peripherals());
  b_.Energize();
  b_.SetPedalTravel(0.5f);  // pedal down, never reached DRIVE
  b_.Run(*app, 500);
  ASSERT_NE(app->state(), lhre::can::VcuStateMachine::kDrive);
  for (const lhal::CanFrame& f : b_.vcu_critical.sent()) {
    if (!msg::InverterTorqueCommand::Matches(f.id)) continue;
    const auto cmd = msg::InverterTorqueCommand::FromFrame(f);
    EXPECT_FALSE(cmd.enable);
    EXPECT_EQ(cmd.torque_request_raw, 0);
  }
}

TEST_F(CriticalTxTest, NoCriticalBusSendsNothingAndKeepsRunning) {
  vcu::Peripherals p = b_.peripherals();
  p.critical_can = nullptr;
  const auto app = std::make_unique<vcu::App>(p);
  b_.Run(*app, 2000);
  EXPECT_TRUE(b_.vcu_critical.sent().empty());
  // Telemetry still runs, and the supervisor keeps feeding the watchdog.
  EXPECT_GT(b_.vcu_data.sent().size(), 0u);
  EXPECT_GT(app->watchdog_refreshes(), 0u);
}

}  // namespace
