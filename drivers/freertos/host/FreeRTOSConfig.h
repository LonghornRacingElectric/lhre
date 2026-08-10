// FreeRTOS configuration for HOST builds (POSIX/Windows simulator ports),
// used by //drivers/freertos:host. Firmware builds use the board's
// CubeMX-generated Core/Inc/FreeRTOSConfig.h instead — keep the feature set
// here a superset of the boards' configs so code that compiles for a board
// also compiles for the host.
//
// Timing note: the simulator ports tick in real time (configTICK_RATE_HZ is
// best-effort under a desktop OS). Tests should assert on ordering and
// counts with generous bounds, never exact tick timing.

#pragma once

#include <assert.h>

#define configUSE_PREEMPTION 1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configUSE_IDLE_HOOK 0
#define configUSE_TICK_HOOK 0
#define configTICK_RATE_HZ ((TickType_t)1000)
// Simulator tasks are real OS threads, and the POSIX port uses the
// FreeRTOS-supplied stack buffer as the pthread stack: it page-aligns the
// buffer's base (16 KB pages on Apple Silicon) and requires what remains to
// be ≥ PTHREAD_STACK_MIN. A buffer smaller than a couple of pages underflows
// that arithmetic and hangs pthread_create with a bogus size — so the host
// minimum is 8192 words (64 KB), not the MCU-style 128. Size task stacks in
// multiples of configMINIMAL_STACK_SIZE and they scale correctly per config.
#define configMINIMAL_STACK_SIZE ((unsigned short)8192)
#define configMAX_PRIORITIES (56)
#define configMAX_TASK_NAME_LEN (16)
#define configUSE_TRACE_FACILITY 1
#define configUSE_16_BIT_TICKS 0
#define configIDLE_SHOULD_YIELD 1
#define configUSE_MUTEXES 1
#define configUSE_RECURSIVE_MUTEXES 1
#define configUSE_COUNTING_SEMAPHORES 1
#define configQUEUE_REGISTRY_SIZE 8

// heap_3 wraps the host malloc/free; configTOTAL_HEAP_SIZE is unused.
#define configSUPPORT_STATIC_ALLOCATION 1
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configTOTAL_HEAP_SIZE ((size_t)0)

#define configUSE_CO_ROUTINES 0
#define configMAX_CO_ROUTINE_PRIORITIES (2)

#define configUSE_TIMERS 1
#define configTIMER_TASK_PRIORITY (2)
#define configTIMER_QUEUE_LENGTH 10
#define configTIMER_TASK_STACK_DEPTH configMINIMAL_STACK_SIZE

#define INCLUDE_vTaskPrioritySet 1
#define INCLUDE_uxTaskPriorityGet 1
#define INCLUDE_vTaskDelete 1
#define INCLUDE_vTaskSuspend 1
#define INCLUDE_vTaskDelayUntil 1
#define INCLUDE_vTaskDelay 1
#define INCLUDE_xTaskGetSchedulerState 1
#define INCLUDE_xTimerPendFunctionCall 1
#define INCLUDE_xQueueGetMutexHolder 1
#define INCLUDE_uxTaskGetStackHighWaterMark 1
#define INCLUDE_xTaskGetCurrentTaskHandle 1
#define INCLUDE_eTaskGetState 1

// Fail loudly under a debugger / in CI instead of the firmware spin-loop.
#define configASSERT(x) assert(x)
