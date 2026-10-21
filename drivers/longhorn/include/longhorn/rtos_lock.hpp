#pragma once

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

namespace longhorn {

// Scheduler-less tests are single-threaded and skip the mutex.
// Check locked() when a timed lock can fail.
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

}  // namespace longhorn
