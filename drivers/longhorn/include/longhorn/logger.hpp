#pragma once

#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "FreeRTOS.h"
#include "lhal/system.hpp"
#include "lhal/uart.hpp"
#include "queue.h"
#include "semphr.h"
#include "task.h"

namespace longhorn {

// Thread-safe, non-blocking logging over any lhal::Uart stream.
//
// Producers format the whole line on their own stack — stamped
// "[<ms>] [LEVEL] " with ANSI color on the level tag, CRLF-terminated — and
// enqueue it without blocking; when the queue is full the line is dropped
// and counted (see dropped()) rather than stalling a control task. A
// dedicated low-priority drain task owns the stream and writes queued lines
// out, so a slow transport (USB VCP, UART) never back-pressures callers.
//
// Header-only on purpose: FreeRTOS.h resolves against the consumer's kernel
// — the board's FreeRTOSConfig.h + family port in firmware,
// //drivers/freertos:host in tests — the same way board app code uses the
// RTOS. On the MCU give the instance static storage duration and call
// StartTask() before vTaskStartScheduler().
//
// Not ISR-safe: call Log()/Info()/... from tasks only. Producers need
// ~kMessageSize bytes of spare stack for the format buffer.
class Logger {
 public:
  enum class Level : uint8_t { kInfo, kSuccess, kWarning, kError };

  // Full line budget, CRLF and terminator included.
  static constexpr size_t kMessageSize = 256;
  static constexpr size_t kQueueLength = 8;

  Logger(lhal::Uart* stream, lhal::Clock* clock,
         SemaphoreHandle_t mutex = nullptr, uint32_t write_timeout_ms = 100)
      : stream_(stream),
        clock_(clock),
        mutex_(mutex),
        write_timeout_ms_(write_timeout_ms) {
    queue_ = xQueueCreateStatic(kQueueLength, sizeof(Message), queue_storage_,
                                &queue_control_);
  }

  ~Logger() {
    if (queue_ != nullptr) {
      vQueueDelete(queue_);
    }
  }

  Logger(const Logger&) = delete;
  Logger& operator=(const Logger&) = delete;

  __attribute__((format(printf, 3, 4))) void Log(Level level,
                                                 const char* format, ...) {
    va_list args;
    va_start(args, format);
    VLog(level, format, args);
    va_end(args);
  }

  __attribute__((format(printf, 2, 3))) void Info(const char* format, ...) {
    va_list args;
    va_start(args, format);
    VLog(Level::kInfo, format, args);
    va_end(args);
  }

  __attribute__((format(printf, 2, 3))) void Success(const char* format, ...) {
    va_list args;
    va_start(args, format);
    VLog(Level::kSuccess, format, args);
    va_end(args);
  }

  __attribute__((format(printf, 2, 3))) void Warning(const char* format, ...) {
    va_list args;
    va_start(args, format);
    VLog(Level::kWarning, format, args);
    va_end(args);
  }

  __attribute__((format(printf, 2, 3))) void Error(const char* format, ...) {
    va_list args;
    va_start(args, format);
    VLog(Level::kError, format, args);
    va_end(args);
  }

  void VLog(Level level, const char* format, va_list args) {
    if (stream_ == nullptr || !stream_->connected() || queue_ == nullptr) {
      return;
    }
    Message msg;
    int prefix_len = std::snprintf(
        msg.text, kMessageSize - 2, "[%lu] %s ",
        clock_ != nullptr ? static_cast<unsigned long>(clock_->Millis()) : 0ul,
        LevelTag(level));
    if (prefix_len < 0 || prefix_len >= static_cast<int>(kMessageSize - 2)) {
      return;
    }
    int body_len = std::vsnprintf(msg.text + prefix_len,
                                  kMessageSize - 2 - prefix_len, format, args);
    if (body_len < 0) {
      return;
    }
    size_t len =
        static_cast<size_t>(prefix_len) + static_cast<size_t>(body_len);
    if (len > kMessageSize - 3) {
      len = kMessageSize - 3;  // vsnprintf reported the untruncated length
    }
    msg.text[len] = '\r';
    msg.text[len + 1] = '\n';
    msg.text[len + 2] = '\0';
    if (xQueueSend(queue_, &msg, 0) != pdTRUE) {
      dropped_ = dropped_ + 1;
    }
  }

  // Creates the statically-allocated drain task. Low priority by default:
  // logging should lose the CPU to everything that matters.
  TaskHandle_t StartTask(UBaseType_t priority = tskIDLE_PRIORITY + 1) {
    task_ = xTaskCreateStatic(&Logger::TaskEntry, "logger", kTaskStackDepth,
                              this, priority, task_stack_, &task_control_);
    return task_;
  }

  // Writes at most one queued line, waiting up to max_wait for one to
  // arrive. The task loop calls this forever; scheduler-less code (tests,
  // superloop builds) can call it directly with a zero wait.
  bool DrainOne(TickType_t max_wait) {
    Message msg;
    if (xQueueReceive(queue_, &msg, max_wait) != pdTRUE) {
      return false;
    }
    if (stream_ != nullptr && stream_->connected()) {
      const bool lock = (mutex_ != nullptr &&
                         xTaskGetSchedulerState() == taskSCHEDULER_RUNNING);
      if (lock) {
        xSemaphoreTake(mutex_, portMAX_DELAY);
      }
      stream_->Write(reinterpret_cast<const uint8_t*>(msg.text),
                     std::strlen(msg.text), write_timeout_ms_);
      if (lock) {
        xSemaphoreGive(mutex_);
      }
    }
    return true;
  }

  // True when the transport is connected and receiving data.
  bool connected() const { return stream_ != nullptr && stream_->connected(); }

  // Lines discarded because the queue was full. Approximate under
  // concurrent producers.
  uint32_t dropped() const { return dropped_; }

 private:
  struct Message {
    char text[kMessageSize];
  };

  static constexpr uint32_t kTaskStackDepth = configMINIMAL_STACK_SIZE + 128;

  static void TaskEntry(void* self) {
    auto* logger = static_cast<Logger*>(self);
    for (;;) {
      logger->DrainOne(portMAX_DELAY);
    }
  }

  static const char* LevelTag(Level level) {
    switch (level) {
      case Level::kSuccess:
        return "\x1b[0;32m[SUCCESS]\x1b[0m";
      case Level::kWarning:
        return "\x1b[0;33m[WARNING]\x1b[0m";
      case Level::kError:
        return "\x1b[0;31m[ERROR]\x1b[0m";
      case Level::kInfo:
      default:
        return "\x1b[0;34m[INFO]\x1b[0m";
    }
  }

  lhal::Uart* stream_;
  lhal::Clock* clock_;
  SemaphoreHandle_t mutex_;
  uint32_t write_timeout_ms_;

  QueueHandle_t queue_ = nullptr;
  StaticQueue_t queue_control_;
  uint8_t queue_storage_[kQueueLength * sizeof(Message)];

  TaskHandle_t task_ = nullptr;
  StaticTask_t task_control_;
  StackType_t task_stack_[kTaskStackDepth];

  volatile uint32_t dropped_ = 0;
};

}  // namespace longhorn
