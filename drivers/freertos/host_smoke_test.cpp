// Smallest possible kernel-under-simulator-port check: one task, one delay,
// end the scheduler. Exists so a broken port/config shows up here — with the
// stderr breadcrumbs telling you how far startup got — instead of as a hang
// in a board's task-level test.

#include <gtest/gtest.h>

#include <cstdio>

#include "FreeRTOS.h"
#include "task.h"

namespace {

void MainTask(void* /*unused*/) {
  std::fprintf(stderr, "smoke: task running\n");
  vTaskDelay(pdMS_TO_TICKS(50));
  std::fprintf(stderr, "smoke: delay done at tick %u\n",
               static_cast<unsigned>(xTaskGetTickCount()));
  vTaskEndScheduler();
}

TEST(FreertosHost, SchedulerRunsTicksAndEnds) {
  static StaticTask_t tcb;
  static StackType_t stack[configMINIMAL_STACK_SIZE];
  std::fprintf(stderr, "smoke: creating task\n");
  xTaskCreateStatic(MainTask, "main", configMINIMAL_STACK_SIZE, nullptr,
                    tskIDLE_PRIORITY + 1, stack, &tcb);
  std::fprintf(stderr, "smoke: starting scheduler\n");
  vTaskStartScheduler();  // returns via vTaskEndScheduler()
  std::fprintf(stderr, "smoke: scheduler ended\n");
  EXPECT_GE(xTaskGetTickCount(), 1u);
}

}  // namespace
