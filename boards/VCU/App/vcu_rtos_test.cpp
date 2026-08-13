// Runs the VCU's real FreeRTOS tasks under the kernel's simulator port
// (POSIX/Windows — see drivers/freertos) against LHAL host fakes.
//
// This complements vcu_app_test: that one drives the scheduler-less Step()
// path with a TestClock for exact, deterministic timing; this one checks the
// actual task wiring (StartTasks, priorities, vTaskDelayUntil cadence). The
// simulator ports tick in real time, so all assertions are lower bounds —
// a stalled CI machine can only make the tasks catch up and produce *more*
// iterations, never fewer.
//
// One scheduler run per process: the kernel's static state isn't reusable
// after vTaskEndScheduler(), so keep everything in this single test.

#include <gtest/gtest.h>

#include "FreeRTOS.h"
#include "lhal/host/can.hpp"
#include "lhal/host/gpio.hpp"
#include "lhal/host/system.hpp"
#include "lhre_can.hpp"
#include "task.h"
#include "vcu_app.hpp"

namespace {

// Long enough for a handful of 100 ms status broadcasts, short enough to keep
// the test snappy.
constexpr uint32_t kRunMs = 350;

void StopTaskEntry(void* /*unused*/) {
  vTaskDelay(pdMS_TO_TICKS(kRunMs));
  vTaskEndScheduler();
}

TEST(VcuRtos, StatusTaskRunsUnderScheduler) {
  lhal::host::SystemClock clock;
  lhal::host::Gpio led;
  lhal::host::CanNetwork network;
  lhal::host::Can vcu_can(&network);
  lhal::host::Can dash(&network);  // observer node, e.g. the dashboard

  vcu::Peripherals p;
  p.clock = &clock;
  p.status_led = &led;
  p.can = &vcu_can;
  vcu::App app(p);

  app.StartTasks();

  // Highest priority so the stop deadline preempts the app's tasks.
  static StaticTask_t stop_tcb;
  static StackType_t stop_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(StopTaskEntry, "stop", configMINIMAL_STACK_SIZE, nullptr,
                    configMAX_PRIORITIES - 1, stop_stack, &stop_tcb);

  vTaskStartScheduler();  // returns when StopTaskEntry ends the scheduler

  // 350 ms at a 100 ms period, first broadcast immediate: expect ~4; ≥2
  // allows heavy scheduler-start jitter.
  EXPECT_GE(app.statuses_sent(), 2u);

  // The observer saw exactly what was sent, decodable with the generated
  // bindings.
  lhal::CanFrame frame;
  uint32_t received = 0;
  while (dash.Receive(&frame)) {
    ASSERT_TRUE(lhre::can::VcuStatus::Matches(frame.id));
    ASSERT_EQ(frame.len, lhre::can::VcuStatus::kDlc);
    EXPECT_EQ(lhre::can::VcuStatus::FromFrame(frame).state,
              lhre::can::VcuState::kIdle);
    ++received;
  }
  EXPECT_EQ(received, app.statuses_sent());
}

}  // namespace
