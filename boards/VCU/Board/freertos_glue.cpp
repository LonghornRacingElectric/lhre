// FreeRTOS ↔ CubeMX glue for the SysTick interrupt.
//
// With FreeRTOS enabled in the .ioc, CubeMX moves the HAL timebase to TIM20
// (Core/Src/stm32g4xx_hal_timebase_tim.c) and stops generating the
// SVC/PendSV/SysTick handlers in stm32g4xx_it.c. SVC and PendSV are mapped
// straight to the kernel port via #defines in the generated FreeRTOSConfig.h;
// SysTick's handler would normally come from CubeMX's cmsis_os2.c, which we
// don't compile (raw FreeRTOS API — see the BUILD file), so it lives here.
// SysTick itself stays untouched until vTaskStartScheduler() configures it.

#include "FreeRTOS.h"
#include "task.h"

extern "C" void xPortSysTickHandler(void);  // defined in the kernel port

extern "C" void SysTick_Handler(void) {
  if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
    xPortSysTickHandler();
  }
}
