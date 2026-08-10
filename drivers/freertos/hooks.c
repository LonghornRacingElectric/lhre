// Static-allocation memory callbacks the kernel requires when
// configSUPPORT_STATIC_ALLOCATION == 1: FreeRTOS asks the application for
// the idle and timer task memory instead of allocating it. CubeMX's
// cmsis_os2.c normally provides these; we use the raw FreeRTOS API, so they
// live here and are compiled into every build that compiles the kernel
// (firmware via //drivers/stm32/<family>:freertos_srcs, host via
// //drivers/freertos:host).

#include "FreeRTOS.h"
#include "task.h"
#include "timers.h"

#if (configSUPPORT_STATIC_ALLOCATION == 1)

void vApplicationGetIdleTaskMemory(
    StaticTask_t** ppxIdleTaskTCBBuffer, StackType_t** ppxIdleTaskStackBuffer,
    configSTACK_DEPTH_TYPE* puxIdleTaskStackSize) {
  static StaticTask_t idle_tcb;
  static StackType_t idle_stack[configMINIMAL_STACK_SIZE];
  *ppxIdleTaskTCBBuffer = &idle_tcb;
  *ppxIdleTaskStackBuffer = idle_stack;
  *puxIdleTaskStackSize = configMINIMAL_STACK_SIZE;
}

#if (configUSE_TIMERS == 1)
void vApplicationGetTimerTaskMemory(
    StaticTask_t** ppxTimerTaskTCBBuffer, StackType_t** ppxTimerTaskStackBuffer,
    configSTACK_DEPTH_TYPE* puxTimerTaskStackSize) {
  static StaticTask_t timer_tcb;
  static StackType_t timer_stack[configTIMER_TASK_STACK_DEPTH];
  *ppxTimerTaskTCBBuffer = &timer_tcb;
  *ppxTimerTaskStackBuffer = timer_stack;
  *puxTimerTaskStackSize = configTIMER_TASK_STACK_DEPTH;
}
#endif  // configUSE_TIMERS

#endif  // configSUPPORT_STATIC_ALLOCATION
