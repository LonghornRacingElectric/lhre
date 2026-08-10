# FreeRTOS

One FreeRTOS kernel version for everything: firmware compiles it with a
Cortex-M port, host tests and sims compile *the same pinned checkout* with
the kernel's simulator ports (POSIX on Linux/macOS, MSVC-MingW on Windows).
Task code written against the raw FreeRTOS API runs unmodified on the host —
that, not a mock scheduler, is the RTOS host-testing story.

| Target             | What                                                   |
| ------------------ | ------------------------------------------------------ |
| `:host`            | The kernel built for the host: simulator port (by `select()` on the OS), `heap_3` (wraps the host malloc), and `host/FreeRTOSConfig.h`. Host tests/sims get FreeRTOS by depending on this. |
| `:hooks`           | `hooks.c` — static-allocation memory callbacks (idle/timer task memory). Compiled into every build that compiles the kernel. |
| `:cubemx_glue`     | `cubemx_glue.c` — `SysTick_Handler` forwarding to `xPortSysTickHandler()`. Family-agnostic; wired into firmware by `enable_freertos`, never built for the host. |
| `:cmsis_os_stub`   | Empty `cmsis_os.h` satisfying the include in CubeMX-generated `main.c`, so boards don't track CubeMX's CMSIS-RTOS2 wrapper headers. Wired in by `enable_freertos`. |
| `:host_smoke_test` | Smallest kernel-under-simulator-port check (one task, one delay, clean scheduler end). A broken port/config shows up here first. |

`deps.bzl` pins the kernel (`@freertos_kernel`, FreeRTOS-Kernel V11.1.0) and
`freertos_kernel.BUILD` overlays Bazel targets onto it. It lives here, not in
a per-family package, precisely so firmware and host can't drift apart.

## How firmware gets the kernel

Firmware does **not** use `:host`. The kernel sources must compile inside
each firmware binary, where that board's CubeMX-generated
`Core/Inc/FreeRTOSConfig.h` is on the include path — the same
sources-not-a-library pattern as the ST HAL. The per-family package bundles
kernel + MCU port + `heap_4` + `:hooks` as
`//drivers/stm32/<family>:freertos_srcs` / `:freertos_headers`, and
`firmware_project(enable_freertos = True)` wires them in — along with
`:cubemx_glue` (the SysTick forwarding handler) and `:cmsis_os_stub`, so
the CubeMX integration lives here once instead of being copy-pasted per
board. The remaining per-board contract (CubeMX settings, what to exclude)
is documented in [tools/firmware](../../tools/firmware/README.md); the
worked example is [boards/VCU](../../boards/VCU/README.md).

CubeMX also vendors its own kernel copy plus the CMSIS-RTOS2 wrapper under
`boards/*/Middlewares/` when FreeRTOS is enabled in the `.ioc`. All of it
is gitignored and never compiled (it's an older kernel and has no simulator
ports); the generated `Core/Src/main.c` does `#include "cmsis_os.h"`
(ST-specific; exists nowhere upstream), which `:cmsis_os_stub` satisfies.
We use the raw FreeRTOS API, not CMSIS-RTOS2.

## Testing RTOS code on the host

The simulator ports run each task as a real OS thread but schedule them
FreeRTOS-style, one at a time. `vTaskEndScheduler()` makes
`vTaskStartScheduler()` return, so a gtest can run the scheduler for a
bounded window:

```cpp
void StopTaskEntry(void*) {
  vTaskDelay(pdMS_TO_TICKS(350));
  vTaskEndScheduler();
}
// in the test: create app tasks + this stop task, vTaskStartScheduler(),
// then assert on what the tasks produced.
```

Two rules keep these tests honest (see `//boards/VCU:vcu_rtos_test`):

- **One scheduler run per process.** The kernel's static state isn't
  reusable after `vTaskEndScheduler()`.
- **Assert lower bounds, never exact timing.** The simulator tick is
  best-effort under a desktop OS; a stalled CI machine makes periodic tasks
  catch up (more iterations), never fewer. Exact-timing coverage belongs in
  scheduler-less unit tests driven by a `TestClock`
  (see [drivers/lhal](../lhal/README.md)).

## Gotchas

- **Task stacks must be big on the host.** The POSIX port uses the
  FreeRTOS-supplied stack buffer as the actual pthread stack, page-aligns
  its base (16 KB pages on Apple Silicon), and needs ≥ `PTHREAD_STACK_MIN`
  left over; too-small buffers hang `pthread_create`. That's why
  `host/FreeRTOSConfig.h` sets `configMINIMAL_STACK_SIZE` to 8192 words —
  size task stacks as multiples of `configMINIMAL_STACK_SIZE` and they come
  out right on both platforms.
- **`hooks.c` is mandatory** whenever `configSUPPORT_STATIC_ALLOCATION=1`:
  it supplies the idle/timer task memory that CubeMX's (uncompiled)
  `cmsis_os2.c` would otherwise provide. It's already inside both `:host`
  and every family's `freertos_srcs`.
- **The POSIX port is patched** (`patches/freertos_posix_event_wait_cancel.patch`):
  upstream (through at least V11.2.0), a task cancelled while suspended dies
  holding its event mutex — `pthread_cond_wait` is a cancellation point that
  re-acquires the mutex, and the port has no cleanup handler — deadlocking
  `vTaskEndScheduler()` on glibc (it deletes the idle/timer tasks since
  V11). macOS resolves the race differently, so the hang only shows on the
  remote Linux executors. Re-check the patch when bumping the kernel pin.
- **Windows is best-effort.** The MSVC-MingW simulator port is wired up but
  untested under the hermetic clang toolchain; if it misbehaves, note that
  the default `bazel test` config runs host tests on remote Linux executors
  anyway (see the [LHAL README](../lhal/README.md#running-tests)).
