/* FreeRTOS <-> CubeMX glue for the SysTick interrupt. Family-agnostic;
 * firmware_project(enable_freertos = True) compiles this into every
 * FreeRTOS firmware binary, so boards don't carry a copy.
 *
 * With FreeRTOS enabled in the .ioc, CubeMX moves the HAL timebase to a TIM
 * and stops generating the SVC/PendSV/SysTick handlers in stm32*_it.c. SVC
 * and PendSV are mapped straight to the kernel port via #defines in the
 * generated FreeRTOSConfig.h; SysTick's handler would normally come from
 * CubeMX's cmsis_os2.c, which we don't compile (raw FreeRTOS API), so it
 * lives here. SysTick itself stays untouched until vTaskStartScheduler()
 * configures it. */

#include "FreeRTOS.h"
#include "task.h"

void xPortSysTickHandler(void); /* defined in the kernel port */

void SysTick_Handler(void) {
  if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
    xPortSysTickHandler();
  }
}
