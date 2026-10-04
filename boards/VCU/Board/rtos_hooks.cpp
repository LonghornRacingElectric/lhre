// FreeRTOS failure hooks (enabled in Core/Inc/FreeRTOSConfig.h USER CODE).
// Both are unrecoverable: put every output in its safe state, then halt with
// interrupts off. The supervisor stops feeding the IWDG, so the watchdog
// resets the MCU and the reset cause reads back as IWDG.

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
