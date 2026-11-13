// Leave outputs safe and let the watchdog reset after an RTOS failure.

#include "FreeRTOS.h"
#include "board_safe_state.hpp"
#include "task.h"

extern "C" void vApplicationStackOverflowHook(TaskHandle_t task, char* name) {
  (void)task;
  (void)name;
  vcu_board::ForceSafeOutputs();
  taskDISABLE_INTERRUPTS();
  for (;;) {
  }
}

extern "C" void vApplicationMallocFailedHook(void) {
  vcu_board::ForceSafeOutputs();
  taskDISABLE_INTERRUPTS();
  for (;;) {
  }
}
