#pragma once

#include <gtest/gtest.h>

#include "FreeRTOS.h"
#include "app/vcu_app.hpp"
#include "lhal/can.hpp"
#include "lhal/host/can.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/watchdog.hpp"
#include "task.h"

namespace vcu::test {

// A bus whose Send never returns, like a driver stuck on a dead peripheral.
// The task that sends on it stops making progress.
class StuckCan final : public lhal::CanBus {
 public:
  lhal::Status Send(const lhal::CanFrame& /*frame*/) override {
    for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
  }
  bool Receive(lhal::CanFrame* /*out*/) override { return false; }
  void SetRxCallback(RxCallback /*callback*/, void* /*context*/) override {}
};

// Watchdog feeds counted once every task deadline has long passed, and again
// at the end of the run. The simulator ticks in real time, so the tests
// compare the two counts instead of bounding either by a time.
struct WatchdogFeeds {
  uint32_t after_deadlines = 0;
  uint32_t at_end = 0;
};

inline constexpr uint32_t kSampleMs = 450;  // past kTelemetryDeadlineMs
inline constexpr uint32_t kRunMs = 600;
static_assert(kSampleMs > App::kTelemetryDeadlineMs + App::kWatchdogTimeoutMs);

struct StallRun {
  lhal::host::Watchdog watchdog;
  WatchdogFeeds feeds;
};

inline void SampleThenEndScheduler(void* context) {
  auto* run = static_cast<StallRun*>(context);
  vTaskDelay(pdMS_TO_TICKS(kSampleMs));
  run->feeds.after_deadlines = run->watchdog.refresh_count();
  vTaskDelay(pdMS_TO_TICKS(kRunMs - kSampleMs));
  run->feeds.at_end = run->watchdog.refresh_count();
  vTaskEndScheduler();
}

// Runs the real tasks on the given buses. One scheduler run per process.
inline WatchdogFeeds RunTasksWith(lhal::CanBus* critical, lhal::CanBus* data) {
  static lhal::host::SystemClock clock;
  static StallRun run;

  Peripherals p;
  p.clock = &clock;
  p.critical_can = critical;
  p.data_can = data;
  p.watchdog = &run.watchdog;
  static App app(p);
  app.StartTasks();

  static StaticTask_t stop_tcb;
  static StackType_t stop_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(SampleThenEndScheduler, "stop", configMINIMAL_STACK_SIZE,
                    &run, configMAX_PRIORITIES - 1, stop_stack, &stop_tcb);
  vTaskStartScheduler();

  EXPECT_TRUE(run.watchdog.started());
  return run.feeds;
}

}  // namespace vcu::test
