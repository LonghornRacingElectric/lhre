# VCU

Vehicle Control Unit firmware (STM32G474) — and the reference board layout
for the repo.

## Targets

| Target           | What                                                        |
| ---------------- | ----------------------------------------------------------- |
| `:vcu`           | Firmware ELF (plus `:vcu.elf` / `:vcu.hex` / `:vcu.bin`).   |
| `:openocd`       | `bazel run` — flash over ST-Link.                           |
| `:dfu`           | `bazel run` — flash over USB DFU.                           |
| `:vcu_app_test`  | Deterministic app-logic test: scheduler-less `Step()` path, TestClock-driven, exact timing asserts. |
| `:vcu_rtos_test` | The real FreeRTOS tasks under the simulator-port scheduler; lower-bound asserts only. |
| `:vcu_sim`       | Interactive host sim running the real tasks (`bazel run --config=local`). |
| `:release`       | elf/bin/hex bundle for CI artifacts.                        |

## Layout

- `App/` — application logic (`vcu::App`), a plain `cc_library` depending
  on `//drivers/lhal` plus the raw FreeRTOS API. The same code links into
  the firmware, the host tests, and the sim; see the
  [LHAL README](../../drivers/lhal/README.md) and
  [drivers/freertos](../../drivers/freertos/README.md) for the pattern.
- `Board/` — hand-written bring-up: `main.cpp` configures clocks, pins, and
  peripheral handles at the ST HAL level, wraps them in LHAL adapters,
  hands them to the app, and starts the scheduler; `freertos_glue.cpp`
  forwards SysTick to the kernel. Owned by us; formatted normally.
- `Core/` — CubeMX-generated from `VCU.ioc`; "Generate Code" is safe to run
  any time. `VCU.ioc` sets `ProjectManager.NoMain=true`, so the generated
  `main.c` has no `main()` — it provides `SystemClock_Config()` and
  `Error_Handler()`, which the firmware compiles and uses (clock changes
  made in CubeMX apply automatically; `lhal::stm32::InitCore()` calls the
  generated clock config). Hand-edit only inside `USER CODE` sections.
- `STM32G474XX_FLASH.ld` / `startup_stm32g474xx.s` — linker script and
  startup for the G474, passed to `firmware_project`.

The `BUILD.bazel` here is the canonical example of a `firmware_project`
call — what it generates and every option is documented in
[tools/firmware](../../tools/firmware/README.md).

## FreeRTOS

FreeRTOS is enabled in `VCU.ioc` (CMSIS_V2 interface — the only kind CubeMX
offers), which makes CubeMX generate `Core/Inc/FreeRTOSConfig.h`, move the
HAL timebase to TIM20, and stop generating the SysTick/SVC/PendSV handlers.
The app uses the **raw FreeRTOS API** on top of that:

- Tasks are created in `App/vcu_app.cpp` (`StartTasks()`, statically
  allocated — one task per periodic activity), and `Board/main.cpp` calls
  `vTaskStartScheduler()`. CubeMX's CMSIS-RTOS2 glue
  (`Core/Src/app_freertos.c` and its `defaultTask`) is deliberately not
  compiled; the vendored kernel under `Middlewares/` isn't either (the
  kernel comes from [drivers/freertos](../../drivers/freertos/README.md),
  shared with the host tests — only the CMSIS-RTOS2 wrapper headers are
  tracked there, because the generated `main.c` includes `cmsis_os.h`).
- Everything the tasks touch is `static` in `main()`: the Cortex-M port
  reclaims `main()`'s stack for interrupts when the scheduler starts.
- Any ISR that calls a `...FromISR` FreeRTOS API must run at NVIC priority
  ≥ `configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY` (5, set in the `.ioc`) —
  that includes future LHAL CAN/UART callback ISRs if tasks are woken from
  them.
- App code keeps the scheduler-less `Step()` path alongside the tasks:
  `:vcu_app_test` uses it for exact, deterministic timing tests, while
  `:vcu_rtos_test` runs the real tasks under the simulator port (see
  [drivers/freertos](../../drivers/freertos/README.md#testing-rtos-code-on-the-host)).
