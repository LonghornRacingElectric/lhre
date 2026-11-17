// Tests for SchedulerLock. Before the scheduler runs (unit tests, early
// boot) it must not touch the mutex; once it runs it must hold it, and a
// timed lock that can't get it must say so and must not release it.
//
// The SchedulerLockRtos test starts the scheduler, so it stays the last
// test in this binary (the kernel's state isn't reusable afterwards).

#include "longhorn/rtos_lock.hpp"

#include <gtest/gtest.h>

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

namespace {

using longhorn::SchedulerLock;

// A mutex's count is 1 while it's free and 0 while someone holds it.

TEST(SchedulerLock, WithoutTheSchedulerANullMutexIsLocked) {
  SchedulerLock lock(nullptr, 0);
  EXPECT_TRUE(lock.locked());
}

TEST(SchedulerLock, WithoutTheSchedulerTheMutexIsLeftAlone) {
  SemaphoreHandle_t mutex = xSemaphoreCreateMutex();
  ASSERT_NE(mutex, nullptr);
  {
    SchedulerLock lock(mutex, 0);
    EXPECT_TRUE(lock.locked());
    EXPECT_EQ(uxSemaphoreGetCount(mutex), 1u);
  }
  EXPECT_EQ(uxSemaphoreGetCount(mutex), 1u);
  vSemaphoreDelete(mutex);
}

struct RtosResult {
  bool first_locked = false;
  bool held_inside = false;
  bool second_locked = true;
  bool still_held_after_failed_lock = false;
  bool free_after = false;
};

SemaphoreHandle_t g_mutex = nullptr;
RtosResult g_result;

void LockEntry(void* /*context*/) {
  {
    SchedulerLock first(g_mutex, 0);
    g_result.first_locked = first.locked();
    g_result.held_inside = uxSemaphoreGetCount(g_mutex) == 0;
    {
      // The mutex isn't recursive, so a second timed lock fails...
      SchedulerLock second(g_mutex, 0);
      g_result.second_locked = second.locked();
    }
    // ...and its destructor must not have released the first one's hold.
    g_result.still_held_after_failed_lock = uxSemaphoreGetCount(g_mutex) == 0;
  }
  g_result.free_after = uxSemaphoreGetCount(g_mutex) == 1;
  vTaskEndScheduler();
}

TEST(SchedulerLockRtos, HoldsTheMutexAndReportsATimedOutLock) {
  g_mutex = xSemaphoreCreateMutex();
  ASSERT_NE(g_mutex, nullptr);
  static StaticTask_t tcb;
  static StackType_t stack[configMINIMAL_STACK_SIZE];
  xTaskCreateStatic(LockEntry, "lock", configMINIMAL_STACK_SIZE, nullptr,
                    tskIDLE_PRIORITY + 1, stack, &tcb);
  vTaskStartScheduler();  // returns when LockEntry ends the scheduler

  EXPECT_TRUE(g_result.first_locked);
  EXPECT_TRUE(g_result.held_inside);
  EXPECT_FALSE(g_result.second_locked);
  EXPECT_TRUE(g_result.still_held_after_failed_lock);
  EXPECT_TRUE(g_result.free_after);
}

}  // namespace
