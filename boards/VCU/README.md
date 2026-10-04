# VCU

Vehicle Control Unit firmware for the 2026-27 car: VCU Rev A (STM32G474VET6,
schematic in `lhre-hardware/vehicle-control-unit`). Also the reference
board layout for the repo.

The VCU owns the driver-facing safety decisions: pedal plausibility, the
ready-to-drive sequence and sound, the torque request to the Cascadia
inverter, and the BMS/IMD lamps. It is the *second* safety path. The BSPD
on this board is pure hardware (comparators, RC timer, NOR latch, relay in
the shutdown loop), and nothing in firmware can clear, disarm or bypass it.
The DUI is a plain CAN peripheral this year.

## Targets

| Target                    | What                                                    |
| ------------------------- | ------------------------------------------------------- |
| `:vcu`                    | Vehicle firmware (`vcu::App`), plus `.elf`/`.hex`/`.bin`. |
| `:vcu_test`               | Bring-up test image (`vcu::TestApp`, `VCU_TEST_IMAGE`). No vehicle logic, no inverter frames. |
| `:openocd` / `:openocd_test` | `bazel run`: flash over ST-Link.                      |
| `:dfu` / `:dfu_test`      | `bazel run`: flash over USB DFU (sends `update.`, then dfu-util). |
| `:vcu_logic_test`         | Pure logic: conversions, plausibility, debounce, shutdown loop, torque, SoE, buzzer. |
| `:vcu_state_machine_test` | Every row of the state machine's transition table.      |
| `:vcu_app_test`           | Vehicle app end to end through `Step()` with LHAL fakes. |
| `:vcu_test_app_test`      | Test image end to end.                                  |
| `:vcu_rtos_test`          | Real FreeRTOS tasks under the simulator port.           |
| `:vcu_sim`                | Scripted drive under the simulator (`bazel run --config=local`). |
| `:release`                | Both images' elf/bin/hex for CI artifacts.              |

## Layout

- `App/`: application logic against `//drivers/lhal` plus the raw FreeRTOS
  API, so the same code links into the firmware, the host tests, and the
  sim.
  - Pure, host-testable logic: `analog` (counts → volts, VREFINT
    correction), `pedals` (APPS/BSE plausibility, brake-over-throttle,
    hard-brake cross-check), `steering`, `shutdown_loop`, `faults` (fault
    bits, brake comparator soft latch, BMS/IMD lamp policy),
    `state_machine`, `torque` (ported 2026 torque map, power limit,
    traction control, regen hook), `battery` (SoE, energy), `buzzer`,
    `status_led`.
  - `vcu_config.hpp`: every tunable, with per-event sets.
  - `vcu_io.hpp`: the Board/App contract (ADC ranks, pins, `Peripherals`,
    `BoardServices`).
  - `vcu_app.*`: the vehicle app's tasks. `vcu_test_app.*`: the bring-up
    image. `vcu_test_bench.hpp`: host fakes plus a scripted car, for tests
    and the sim.
- `Board/`: hand-written bring-up at ST HAL level.
  - `main.cpp`: reset cause, clocks, safe outputs, the `MX_*_Init` calls,
    LHAL adapters, and the app.
  - `analog_setup.cpp`, `comms_setup.cpp`: what the `.ioc` doesn't express
    yet (see [IOC gaps](#ioc-gaps)).
  - `fault_store.cpp`: flash persistence and bootloader entry.
  - `board_safe_state.cpp`: the safe output levels, also used from
    `Error_Handler` and the fault handlers.
  - `rtos_hooks.cpp`, `irq_handlers.cpp`.
- `Core/`: CubeMX-generated from `VCU.ioc`. Hand-edit only inside `USER
  CODE` sections.
- `STM32G474XX_FLASH.ld` / `startup_stm32g474xx.s`: linker script and
  startup. The linker script stops 2 KB short of the end of flash: the last
  page of bank 2 holds the brake fault latch.

## Tasks and timing

| Task       | Period | Priority | Job |
| ---------- | ------ | -------- | --- |
| supervisor | 20 ms  | idle+6   | Feeds the IWDG (100 ms) only if control and telemetry both made progress; otherwise forces safe outputs and lets the IWDG reset. |
| control    | 3 ms   | idle+5   | Inputs, plausibility, state machine, torque, outputs, Critical-bus TX (333 Hz, as on the 2026 car). |
| telemetry  | 10 ms  | idle+3   | Data-bus diagnostics, status LED, flash writes for the fault latch. |
| shell ×2, logger | — | idle+1 | Debug consoles on the STDC14 VCP (USART3) and USB CDC. |

## Pin map (Rev A netlist)

| Signal | Pin | Notes |
| --- | --- | --- |
| APPS1/2_Filtered, BSE1/2_Filtered | PC0-PC3 (ADC1 IN6-9) | 5.1k/10k divider + TLV900x follower: sensor V × 0.662 |
| Steering_Filtered | PC4 (ADC2 IN5) | same front end |
| Shutdown_out_S / Shutdown_in_S | PA3 (ADC1 IN4) / PC5 (ADC2 IN11) | 100k/13.7k: loop V × 0.1205; taps downstream/upstream of the BSPD relay |
| B1/B2_Disconnect, B1/B2_Overvoltage | PD13, PD14, PD11, PD15 | comparator flags, high = asserted |
| Hard_braking / combined_condition / fault_confirmed / latch_state (_mcu) | PE7, PE8, PE9, PE10 | BSPD readbacks, high = asserted (latch high = relay open) |
| linelock_out | PD10 | `Linelock_Ctrl AND NOT Hard_braking` |
| motor_power / motor_disconnect | PD12 / PE6 | Schmitt-buffered; no pull resistors on the cable side |
| Ready_to_Drive_SW | PE11 | pulled up, **low = pressed** |
| Linelock_Ctrl / Amp_Shutdown_SW | PE15 / PE14 | high = request / amp on |
| BMS_Error_SW / IMD_Error_SW | PE12 / PE13 | high = lamp on; the board pull-up lights them if the MCU is dead |
| Crit_STB / Data_STB | PD9 / PD8 | TJA1462 standby when high; held low (normal) |
| LED_G / LED_R / LED_B | PA0 / PA1 / PA2 (TIM2 CH1-3) | common anode, PWM active low |
| Audio_DAC | PA4 (DAC1 OUT1) | to the PAM8302 |
| Critical CAN | PB12/PB13 (FDCAN2) | 1 Mbit/s classic |
| Data CAN | PD0/PD1 (FDCAN1) | 1 Mbit/s classic |
| VCP | PC10/PC11 (USART3) | STDC14 virtual COM port, 115200 |
| BOOT0trig | PB7 | high + reset → ROM bootloader (USB DFU) |

## Behavior worth knowing

- **Boot**: the reset cause is read before anything else (reported in
  `VCU_DIAG`, the boot log, and `/state`). Outputs are safe before any
  peripheral starts. The state starts at INIT, then IDLE, and torque is 0.
- **States**: IDLE (LV on) → PRECHARGE → TS_ACTIVE → RTD_WAIT → DRIVE, with
  FAULT and a terminal BSPD_TRIPPED. RTD_WAIT needs:
  - the HVC energized, with both AIRs reading closed;
  - the loop closed per the VCU's taps;
  - no torque-cut fault;
  - the inverter fresh and fault-free;
  - the PDU fresh.

  DRIVE needs a debounced RTD press edge (a switch held at boot doesn't
  count), brake ≥ 30 psi, and the pedal released. Leaving DRIVE for any of
  those reasons goes to FAULT, and getting back needs a fresh RTD
  sequence. The table and its rationale are in `App/state_machine.cpp`.
- **BSPD**: `latch_state_mcu` zeroes torque the same tick and enters
  BSPD_TRIPPED. Only a power cycle clears it, in hardware and in firmware.
- **Brake comparator flags** cut torque through a soft latch. The latch is
  stored in flash, so a power cycle doesn't clear it; only the dash's
  `DUI_DRIVER_INPUTS.fault_reset`, pressed while no flag is active, does.
- **RTD sound**: a 1 kHz sine from the DAC for 1.2 s on entering DRIVE.
  There is a local 1.5 s ceiling, and the amp is cut immediately if the
  shutdown loop opens.
- **BMS/IMD lamps**: firmware only ever adds indication. A flag in
  `HVC_INDICATORS` latches the lamp until power cycle, and a silent HVC
  lights both lamps.
- **Line lock**: `Linelock_Ctrl` is a driven output but stays 0 until regen
  blending exists (`RegenHook`). `VCU_DRIVE_STATE` carries the actual gated
  `linelock_out` (byte 5, which the PDU acts on) and the request (byte 7).
- **Event modes**: `VCU_MODE_COMMAND` (0x029) or `/mode` selects the
  acceleration, skidpad, autocross or endurance parameters. The change
  applies only outside DRIVE and is echoed in `VCU_DRIVE_STATE.event_mode`.
- **Bootloader**: typing `update.` on either console (what `tools/dfu`
  sends) reboots into the ST ROM DFU bootloader through BOOT0trig. It is
  refused while the tractive system is up.

CAN IDs, layouts, and buses are defined in [lib/spec](../../lib/spec/README.md)
(see the [CAN matrix](../../docs/can-matrix.md)). Critical-bus content keeps
the 2026 IDs and layouts. New diagnostics (`VCU_ADC_*`, `VCU_DIGITAL_IO`,
`VCU_DIAG`, `VCU_CALIBRATED_*`) are on the Data bus.

## Bring-up test image

Flash `:vcu_test` (`bazel run //boards/VCU:openocd_test`), then open the
VCP or the USB port with `bazel run //tools/monitor`.

| Command | Does |
| --- | --- |
| `/adc` | Every analog channel: counts, pin mV, sensor/loop mV, calibrated |
| `/io` | Every digital input and output |
| `/stream <ms>` | Logs all inputs to the VCP every `ms` (0 = off) |
| `/cal start`, `/cal stop` | Min/max/mean per channel while sweeping a sensor (pedal calibration → `App/vcu_config.hpp`) |
| `/out <linelock\|amp\|bms\|imd\|crit_stb\|data_stb> <0\|1>`, `/release` | Drive outputs, or return them all to safe |
| `/tone <hz> <amp%> <ms>`, `/tone off` | DAC tone through the amp (capped at 1.5 s) |
| `/led <r%> <g%> <b%>`, `/led auto` | Status RGB |
| `/wdtest` | Stops feeding the IWDG. Expect outputs safe, then a reset with cause `iwdg`. |
| `/info` | Reset cause, CAN error counters, bus-off count, drops |

Over CAN, the image streams the `VCU_ADC_*`, `VCU_CALIBRATED_*` and
`VCU_DIGITAL_IO` messages at 100 Hz and `VCU_DIAG` at 10 Hz on the Data
bus. It also obeys `BENCH_TEST_COMMAND` (outputs, tone, watchdog test) and
echoes `BENCH_ECHO_REQUEST_CRITICAL`/`_DATA` back on the same bus.

## IOC gaps

The regenerated `VCU.ioc` needs these changes. `Board/` works around each
one at runtime, and each workaround can go once CubeMX generates the
equivalent:

1. `ProjectManager.NoMain=true`. Today `Core/Src/main.c` renames its own
   `main()` away in `USER CODE BEGIN PFP`.
2. PA3 → ADC1_IN4 and PC5 → ADC2_IN11. Today both are GPIO inputs, which
   `analog_setup.cpp` reconfigures as analog.
3. ADC1/ADC2 configuration:
   - TIM6 TRGO trigger;
   - DMA continuous requests;
   - 16× oversampling;
   - ADC1 ranks for IN4 and VREFINT, and ADC2 rank IN11 with DMA on
     DMA1_Ch2;
   - drop the unused AWD3.
4. DAC1 CH1 triggered by TIM7 TRGO, with DMA on DMA1_Ch3. TIM6/TIM7 as
   basic timers with TRGO on update.
5. FDCAN2 (Critical):
   - automatic retransmission on;
   - FDCAN2_IT0/IT1 at priority 5.
6. IWDG. Today the LHAL adapter drives it at register level.
7. USART3 on PC10/PC11 for the STDC14 VCP. Today `comms_setup.cpp` sets it
   up, and `BUILD.bazel` defines `HAL_UART_MODULE_ENABLED`.
8. GPIO user labels, and initial level high on PE12/PE13 (BMS/IMD lamps
   on).
9. Restore the USB strings `LHRe` / `VCU` (`tools/dfu` matches the port by
   "lhre" or VID:PID).
10. FreeRTOS stack-overflow and malloc-failed hooks. Today they are set in
    the `FreeRTOSConfig.h` USER CODE.

The clock tree is unchanged from the 2026 VCU: 16 MHz HSE → 96 MHz SYSCLK,
FDCAN on PCLK1, ADC on HCLK/2 = 48 MHz, USB on PLLQ.

## Open items (verify on hardware)

- The RGB LED D35 pinout: pins 1-3 are named as cathodes but tied to Vdd.
- RTD switch type. The firmware leaves DRIVE when the switch opens, as the
  2026 toggle did (`DriveParams::rtd_release_exits_drive`).
- APPS and BSE sensor ranges and pedal endpoints. Defaults assume 0.5-4.5 V
  sensors; capture the real values with `/cal`.
- Steering mapping constants (carried from the 2026 sensor), and the
  hard-brake trimmer set point (`BseParams::hard_brake_pin_v`).
- Polarity of `motor_disconnect`. Neither motor cable has a pull resistor,
  so an unplugged cable reads undefined; firmware uses those inputs as
  diagnostics only.
- Rule text for every threshold marked `TODO(verify rule)`. The RTD sound
  needs measuring against its 80-90 dBA band.
