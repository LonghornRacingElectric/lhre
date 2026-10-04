#pragma once

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

namespace vcu {

// Locks `mutex` for a scope when the scheduler runs. The scheduler-less
// Step() path is single-threaded and can't take a mutex before the kernel
// starts, so it skips the lock. With a zero wait the lock may fail; check
// locked().
class SchedulerLock {
 public:
  SchedulerLock(SemaphoreHandle_t mutex, TickType_t wait)
      : mutex_(xTaskGetSchedulerState() == taskSCHEDULER_RUNNING ? mutex
                                                                 : nullptr) {
    locked_ = mutex_ == nullptr || xSemaphoreTake(mutex_, wait) == pdTRUE;
  }
  ~SchedulerLock() {
    if (mutex_ != nullptr && locked_) {
      xSemaphoreGive(mutex_);
    }
  }
  SchedulerLock(const SchedulerLock&) = delete;
  SchedulerLock& operator=(const SchedulerLock&) = delete;

  bool locked() const { return locked_; }

 private:
  SemaphoreHandle_t mutex_;
  bool locked_ = false;
};

}  // namespace vcu
