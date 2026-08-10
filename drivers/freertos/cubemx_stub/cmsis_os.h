/* Stub for CubeMX's CMSIS-RTOS2 wrapper header (ST-specific; exists nowhere
 * upstream). With FreeRTOS enabled in the .ioc, the generated Core/Src/main.c
 * unconditionally does #include "cmsis_os.h" — but nothing we compile calls
 * the wrapper: this repo uses the raw FreeRTOS API, and CubeMX's wrapper
 * sources (app_freertos.c, cmsis_os2.c, the vendored kernel under each
 * board's Middlewares/ directory) are never compiled and fully gitignored.
 *
 * firmware_project(enable_freertos = True) puts this header on the include
 * path so boards don't have to track CubeMX's copy.
 *
 * Deliberately empty: any actual CMSIS-RTOS2 usage (osThreadNew, osDelay,
 * ...) should fail to compile. Use the raw FreeRTOS API instead. */

#ifndef LHRE_CUBEMX_STUB_CMSIS_OS_H_
#define LHRE_CUBEMX_STUB_CMSIS_OS_H_

#endif /* LHRE_CUBEMX_STUB_CMSIS_OS_H_ */
