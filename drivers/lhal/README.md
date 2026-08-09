# Longhorn HAL (LHAL)

Platform-independent C++ peripheral interfaces so application code can build
for **both the MCU and the host** — for unit tests, integration tests, and
sims — while keeping ST's HAL underneath on real hardware.

```
        application code (App/…)
                 │  depends only on
                 ▼
        //drivers/lhal  (pure virtual interfaces, no ST headers)
                 │
    ┌────────────┴─────────────┐
    ▼                          ▼
 lhal::stm32::*             lhal::host::*
 adapters over ST HAL       in-memory fakes
 handles (firmware)         (tests & sims)
```

## Interfaces (`include/lhal/*.hpp`)

| Interface         | Covers                                              |
| ----------------- | --------------------------------------------------- |
| `lhal::Gpio`      | digital read/write/toggle                           |
| `lhal::Uart`      | blocking + async read/write (DMA/IT on target)      |
| `lhal::I2cMaster` | write / read / write-then-read (register access)    |
| `lhal::CanBus`    | send, polled receive, RX callback; classic + CAN FD |
| `lhal::Clock`     | `Millis()` / `DelayMs()` (+ wrap-safe `ElapsedMs`)  |

All interfaces are heap-free and exception-free; callbacks are function
pointer + context (they may run in ISR context on target).

## Using it in a board

Application logic lives in its own library against LHAL only (see
`//boards/VCU` for the reference layout):

```python
load("//drivers/lhal:lhal_library.bzl", "lhal_cc_library")

lhal_cc_library(          # cc_library that also gets MCU flags when
    name = "vcu_app",     # cross-compiled, so it links into firmware
    family = "stm32g4",
    deps = ["//drivers/lhal"],
    ...
)

firmware_project(
    name = "vcu",
    extra_deps = [":vcu_app", "//drivers/lhal:stm32_headers"],
    extra_srcs = ["//drivers/lhal:stm32_srcs"],
    ...
)

cc_test(                  # the same app logic, tested on the host
    name = "vcu_app_test",
    deps = [":vcu_app", "//drivers/lhal:host", "@googletest//:gtest_main"],
)
```

The board's `main.cpp` is hand-written (no CubeMX `main.c`): bring-up stays
at the ST HAL level (clocks, pin mux, peripheral handles + MSP), then wraps
the handles in LHAL adapters and hands them to the app:

```cpp
lhal::stm32::Uart debug(&huart1);   // huart1 configured by board code
lhal::stm32::Can  can(&hfdcan1);    // filters configured by board code
can.Start();
vcu::App app({.clock = &clock, .can = &can, ...});
while (true) app.Step();
```

On the host, the same app runs against `lhal::host::*` fakes: an in-memory
`CanNetwork` connecting multiple nodes, injectable UART, fake I2C devices
(`I2cDevice`), and a manually-advanced `TestClock`.

## STM32 backend notes

- Adapters wrap **handles the board configured** (CubeMX-style `MX_*_Init`
  or hand-written). LHAL deliberately does not own peripheral init: pin mux,
  clocks, and DMA channel wiring stay board code.
- Built as sources inside the firmware binary (`stm32_srcs`), exactly like
  the ST HAL sources — so the board's `*_hal_conf.h` and device define
  apply. Each adapter is guarded by its `HAL_*_MODULE_ENABLED`, so boards
  that don't enable a module pay nothing.
- The family header is auto-detected (`__has_include`), so the same backend
  serves stm32g4/h7/f4/f0 boards.
- UART async uses DMA when the handle has a DMA channel linked
  (`HAL_LINKDMA` in the MSP), interrupt mode otherwise. LHAL defines the
  global `HAL_UART_*Callback` / `HAL_FDCAN_RxFifo0Callback` functions to
  route completion — don't define those elsewhere in a board that links
  `stm32_srcs`.
- **Escape hatch:** every adapter exposes `handle()`. Peripherals without an
  LHAL abstraction (SPI, timers, ADC, …) just keep using ST HAL directly —
  the headers are already on the include path.

## Running tests

```bash
bazel test //drivers/lhal:lhal_host_test   # remote (Linux executors)
bazel test --config=local //drivers/lhal:lhal_host_test   # this machine
```

Host tests target Linux on the default (remote) config — see the `test
--platforms` block in `.bazelrc`. Host *binaries* (e.g. `:vcu_sim`) should
be run with `--config=local`.

## Adding a peripheral abstraction

1. Interface in `include/lhal/<peripheral>.hpp` (pure virtual, heap-free).
2. STM32 adapter in `include/lhal/stm32/` (+ `stm32/*.cpp` if it needs
   callback dispatch), guarded by its `HAL_*_MODULE_ENABLED`.
3. Host fake in `include/lhal/host/` with test helpers (inject/inspect).
4. Tests in `test/lhal_host_test.cpp`.
