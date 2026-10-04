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
| `lhal::Pwm`       | duty-cycle control of one PWM output channel        |
| `lhal::Clock`     | `Millis()` / `DelayMs()` (+ wrap-safe `ElapsedMs`)  |
| `lhal::AdcScan`   | latest counts of a continuously sampled ADC bank    |
| `lhal::WaveformDac` | loop a sample buffer at a sample rate (tones)     |
| `lhal::Watchdog`  | start (one-way) and refresh a hardware watchdog     |
| `lhal::ResetCause` | enum: why the MCU last reset (`lhal/reset.hpp`)    |

`lhal::CanBus::stats()` reports bus health (bus-off count, dropped TX,
lost RX, error counters); backends that can't observe a counter leave it
zero.

All interfaces are heap-free and exception-free; callbacks are function
pointer + context (they may run in ISR context on target).

LHAL abstracts *peripherals* only. The RTOS is deliberately not abstracted:
app code uses the raw FreeRTOS API, which is host-testable because the same
kernel builds for the host too — see
[drivers/freertos](../freertos/README.md).

## Using it in a board

Application logic lives in `App/` against LHAL only, and the board's
`firmware_project` call synthesizes the targets from the file names (see
[tools/firmware](../../tools/firmware/README.md) and `//boards/VCU` for the
reference layout): `App/vcu_app.cpp` becomes the `:vcu_app` library — a
plain `cc_library` on `//drivers/lhal`, so it links into firmware and host
tests alike (MCU flags come from the per-family platform/toolchain) — and
each `App/*_test.cpp` becomes a host `cc_test` against `:vcu_app` +
`//drivers/lhal:host`. The STM32 LHAL backend is wired into the firmware
binary by the macro itself.

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
(`I2cDevice`), settable ADC channels, a recording DAC and watchdog, and a
manually-advanced `TestClock`.

## STM32 backend notes

- Adapters wrap **handles the board configured** (CubeMX-style `MX_*_Init`
  or hand-written). LHAL deliberately does not own peripheral init: pin mux,
  clocks, and DMA channel wiring stay board code.
- Built as sources inside the firmware binary (`stm32_srcs`), exactly like
  the ST HAL sources — so the board's `*_hal_conf.h` and device define
  apply. `firmware_project` wires the backend into every firmware binary;
  boards don't reference `stm32_srcs`/`stm32_headers` themselves. Each
  adapter is guarded by its `HAL_*_MODULE_ENABLED`, so boards that don't
  enable a module pay nothing.
- The family header is auto-detected (`__has_include`), so the same backend
  serves stm32g4/h7/f4/f0 boards.
- UART async uses DMA when the handle has a DMA channel linked
  (`HAL_LINKDMA` in the MSP), interrupt mode otherwise. LHAL defines the
  global `HAL_UART_*Callback` / `HAL_FDCAN_RxFifo0Callback` functions to
  route completion — don't define those elsewhere in a board that links
  `stm32_srcs`.
- CAN (`lhal::stm32::Can`): the G4 FDCAN TX FIFO is three frames deep, so
  `Send()` falls back to a 32-frame software queue that the TX-FIFO-empty
  interrupt drains in order (`kBusy` only when both are full, counted as
  `tx_dropped`). Bus-off is recovered automatically and counted. `Start()`
  enables these interrupts on line 0, so the board must enable that
  instance's `FDCANx_IT0` IRQ in the NVIC — CubeMX only does so for
  instances ticked on its NVIC page. LHAL also defines
  `HAL_FDCAN_TxFifoEmptyCallback` and `HAL_FDCAN_ErrorStatusCallback`.
- ADC (`lhal::stm32::DmaAdc`): wraps an ADC the board set up for scan
  conversions into a circular DMA buffer (DMA continuous requests, timer
  trigger). `Start()` calibrates (G4) and starts the DMA.
- DAC (`lhal::stm32::DmaDac`): a DAC channel fed by circular DMA and paced
  by a basic timer's TRGO; `Play()` retunes the timer to the sample rate.
- Watchdog (`lhal::stm32::Iwdg`) and `lhal::stm32::ReadAndClearResetCause()`
  are register-level (no HAL module needed), guarded on the peripheral.
- USB virtual COM port: `lhal::stm32::UsbCdc` implements `lhal::Uart` over
  USB CDC-ACM. Enable USB_Device (CDC) in the board's `.ioc` and set
  `enable_usb = True` on its `firmware_project` — the macro wires in ST's
  middleware (`//drivers/stm32/usb_device`) and the generated `USB_DEVICE/`
  glue, minus `usbd_cdc_if.c`: LHAL defines its `USBD_Interface_fops_FS`
  struct instead, so `MX_USB_Device_Init` registers LHAL's callbacks with
  no edits to generated code. Construct the `UsbCdc` before calling
  `MX_USB_Device_Init`. The adapter is guarded by
  `__has_include("usbd_cdc.h")`, so boards without USB pay nothing.
- **Escape hatch:** every adapter exposes `handle()`. Peripherals without an
  LHAL abstraction (SPI, general timers, …) just keep using ST HAL directly —
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
