// Runs the VCU's real FreeRTOS tasks under the kernel's simulator port
// (POSIX/Windows — see drivers/freertos) against LHAL host fakes.
//
// vcu_app_test drives the scheduler-less Step() path with a TestClock for
// exact timing; this one checks the task wiring (StartTasks, priorities,
// vTaskDelayUntil cadence, the supervisor feeding the watchdog). The
// simulator ticks in real time, so all assertions are lower bounds.
//
// One scheduler run per process: keep everything in this single test.

#include <gtest/gtest.h>

#include "FreeRTOS.h"
#include "app/vcu_app.hpp"
#include "lhal/host/can.hpp"
#include "lhal/host/pwm.hpp"
#include "lhal/host/system.hpp"
#include "lhal/host/watchdog.hpp"
#include "lhre_can_vcu.hpp"
#include "task.h"

namespace {

constexpr uint32_t kRunMs = 350;

void StopTaskEntry(void* /*unused*/) {
  vTaskDelay(pdMS_TO_TICKS(kRunMs));
  vTaskEndScheduler();
}

TEST(VcuRtos, TasksRunUnderScheduler) {
  static lhal::host::SystemClock clock;
  static lhal::host::CanNetwork network;
  static lhal::host::Can vcu_can(&network);
  static lhal::host::Can observer(&network);
  static lhal::host::Watchdog watchdog;
  static lhal::host::Pwm red, green, blue;
  static longhorn::RgbLed led(&red, &green, &blue);

  vcu::Peripherals p;
  p.clock = &clock;
  p.critical_can = &vcu_can;
  p.watchdog = &watchdog;
  p.status_led = &led;
  static vcu::App app(p);

  app.StartTasks();

  // Highest priority so the stop deadline preempts the app's tasks.
  static StaticTask_t stop_tcb;
  static StackType_t stop_stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(StopTaskEntry, "stop", configMINIMAL_STACK_SIZE, nullptr,
                    configMAX_PRIORITIES - 1, stop_stack, &stop_tcb);

  vTaskStartScheduler();  // returns when StopTaskEntry ends the scheduler

  uint32_t inverter = 0;
  uint32_t heartbeats = 0;
  lhal::CanFrame frame;
  while (observer.Receive(&frame)) {
    if (lhre::can::vcu::InverterTorqueCommand::Matches(frame.id)) {
      ++inverter;
      EXPECT_FALSE(
          lhre::can::vcu::InverterTorqueCommand::FromFrame(frame).enable);
    }
    if (lhre::can::vcu::VcuStatus::Matches(frame.id)) {
      ++heartbeats;
    }
  }
  // 350 ms at 3 ms (~116) and 10 ms (~35); generous for scheduler jitter.
  EXPECT_GE(inverter, 30u);
  EXPECT_GE(heartbeats, 10u);
  EXPECT_TRUE(watchdog.started());
  EXPECT_GE(watchdog.refresh_count(), 5u);
  EXPECT_EQ(app.state(), vcu::VcuState::kIdle);
  // Missing vehicle inputs indicate a red fault pulse, possibly in its off
  // phase.
  EXPECT_FLOAT_EQ(green.duty(), 0);
  EXPECT_FLOAT_EQ(blue.duty(), 0);
  EXPECT_LT(red.duty(), 0.5f) << "LED task must replace the boot-white color";
}

}  // namespace
