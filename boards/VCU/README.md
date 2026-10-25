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
| `:openocd` | `bazel run`: flash over ST-Link.                      |
| `:dfu`      | `bazel run`: flash over USB DFU (sends `update.`, then dfu-util). |
| `:vcu_logic_test`         | Pure logic: conversions, plausibility, debounce, shutdown loop, torque, SoE, buzzer. |
| `:vcu_state_machine_test` | Every row of the state machine's transition table.      |
| `:vcu_app_test`           | Vehicle app end to end through `Step()` with LHAL fakes. |
| `:vcu_test_app_test`      | Host bench harness end to end.                                  |
| `:vcu_rtos_test`          | Real FreeRTOS tasks under the simulator port.           |
| `:control_features_test`  | Regen, launch ramp, sampled traction limiter, ADC progress. |
| `:vcu_sim`                | Scripted drive under the simulator (`bazel run --config=local`). |
| `:release`                | Vehicle elf/bin/hex for CI artifacts.              |

## Layout

- `App/`: application logic against `//drivers/lhal` plus the raw FreeRTOS
  API, so the same code links into the firmware, the host tests, and the
  sim.
  - Pure, host-testable logic: `analog` (counts → volts, VREFINT
    correction), `pedals` (APPS/BSE plausibility, brake-over-throttle,
    hard-brake cross-check), `steering`, `shutdown_loop`, `faults` (fault
    bits, brake comparator soft latch, BMS/IMD lamp policy),
    `state_machine`, `torque` (ported 2026 torque map, power limit,
    motor-acceleration limiter), `control_features` (regen and launch ramp), `battery` (SoE, energy), `buzzer`,
    `status_led`.
  - `vcu_config.hpp`: every tunable, with per-event sets.
  - `vcu_io.hpp`: the Board/App contract (ADC ranks, pins, `Peripherals`,
    `BoardServices`).
  - `vcu_app.*`: the vehicle app's tasks. `vcu_test_app.*`: a host bench
    harness retained for integration tests. `vcu_test_bench.hpp`: host fakes
    plus a scripted car, for tests and the sim.
- `Board/`: hand-written bring-up at ST HAL level.
  - `main.cpp`: reset cause, clocks, safe outputs, the `MX_*_Init` calls,
    LHAL adapters, and the app.
  - `analog_setup.cpp`, `comms_setup.cpp`: what the `.ioc` doesn't express
    yet (see [IOC gaps](#ioc-gaps)).
  - `fault_store.cpp`: flash persistence and bootloader entry.
  - `board_safe_state.cpp`: the safe output levels, also used from
    `Error_Handler` and the fault handlers.
  - `rtos_hooks.cpp`, `irq_handlers.cpp`.
- `Core/`: CubeMX-generated from `VCU.ioc`; change through regeneration,
  not hand edits.
- `STM32G474XX_FLASH.ld` / `startup_stm32g474xx.s`: linker script and
  startup. The linker script stops 2 KB short of the end of flash: the last
  page of bank 2 holds the brake fault latch.

## Tasks and timing

| Task       | Period | Priority | Job |
| ---------- | ------ | -------- | --- |
| supervisor | 20 ms  | idle+6   | Feeds the IWDG (100 ms) only if control and telemetry both made progress; otherwise forces safe outputs and lets the IWDG reset. |
| control    | 3 ms   | idle+5   | Inputs, plausibility, state machine, torque, outputs, Critical-bus TX (333 Hz, as on the 2026 car). |
| telemetry  | 10 ms  | idle+3   | Data-bus diagnostics, flash writes for the fault latch. |
| rainbow | 33 ms | idle+2 | VCU LED task; continuous five-second rainbow, as last year. |
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
| LED blue / red / green | PA0 / PA1 / PA2 (TIM2 CH1-3) | D35 rotated 180°; common anode, PWM active low |
| Audio_DAC | PA4 (DAC1 OUT1) | to the PAM8302 |
| Critical CAN | PB12/PB13 (FDCAN2) | 1 Mbit/s classic |
| Data CAN | PD0/PD1 (FDCAN1) | 1 Mbit/s classic |
| VCP | PC10/PC11 (USART3) | STDC14 virtual COM port, 115200 |
| BOOT0trig | PB7 | high + reset → ROM bootloader (USB DFU) |

## Behavior worth knowing

- **Boot**: the reset cause is read before anything else (reported in
  `VCU_DIAG`, the boot log, and `/state`). Outputs are safe before any
  peripheral starts. The state starts at INIT, then IDLE, and torque is 0.
  The board-local `vcu::RgbLed` starts white, then a dedicated static
  `vcu::RainbowTask` runs the five-second rainbow continuously at
  33 ms intervals, as last year's shared LED task did. Only this task
  writes the PWM; BSPD_TRIPPED overrides its color. The rainbow runs
  independently of vehicle CAN freshness.
  TIM2 channel order/polarity and the legacy CCR=125 boot brightness /
  CCR<=127 rainbow brightness are preserved. Unlike the old VCU's CAN
  callback, control never repeatedly creates or destroys LED threads.
- **ADC progress**: each completed DMA scan increments a counter. Either
  bank without progress for 10 ms raises `ADC_STALE` (fault code 21), cuts
  torque, disables the inverter command and leaves DRIVE. Constant sensor
  values are healthy if conversions keep running. This is independent of
  the task-progress watchdog.
- **Inverter feedback**: status, faults, current, voltage and either speed
  frame must be fresh. Losing any required measurement cuts torque and
  requires a new RTD sequence; continued status messages cannot mask it.
  Timeouts remain twice the spec period, including 200 ms for 10 Hz current.
- **Motor disconnect**: PE6 high for 15 ms raises `MOTOR_DISCONNECT`, cuts
  torque, disables the inverter command and leaves DRIVE. Clearing it does
  not restart the car: a new RTD press is required. Bench-check polarity.
  An unplugged cable can float; firmware cannot guarantee open-wire
  detection without an electrical pull resistor or a defined source.
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
- **Brake comparator flags**: B1 disconnect, B2 disconnect, B1 overvoltage
  and B2 overvoltage each cut torque, disable the inverter and exit DRIVE
  after the 10 ms input debounce. They share a soft latch stored in flash,
  so a power cycle doesn't clear it; only the dash's
  `DUI_DRIVER_INPUTS.fault_reset`, pressed while no flag is active, does.
- **RTD sound**: a 1 kHz sine from the DAC for 1.2 s on entering DRIVE.
  There is a local 1.5 s ceiling, and the amp is cut immediately if the
  shutdown loop opens.
- **BMS/IMD lamps**: firmware only ever adds indication. A flag in
  `HVC_INDICATORS` latches the lamp until power cycle, and a silent HVC
  lights both lamps.
- **Line lock**: controlled over CAN to the PDU. Disabled in the default
  configuration along with regen. `VCU_DRIVE_STATE` carries the gated
  `linelock_out` (byte 5, which the PDU acts on) and the request (byte 7).
- **Event modes**: `VCU_MODE_COMMAND` (0x029) or `/mode` selects the
  acceleration, skidpad, autocross or endurance parameters. The change
  applies only outside DRIVE and is echoed in `VCU_DRIVE_STATE.event_mode`.
- **Bootloader**: raw `update.` over USB works with or without a newline,
  across USB packet boundaries, with no vehicle CAN or sensor prerequisite,
  as in last year's shared DFU helper. USB reception detects the token
  before buffering; the high-priority supervisor drives outputs safe,
  sends a zero/disabled inverter request and enters ROM DFU independently
  of the control task. The debug VCP accepts the same token via its shell.
  Each console has its own matcher so partial tokens cannot join across
  ports. The vehicle image supports standalone USB flashing.
  The board selects flash bank 1 if needed (the old `ota_set_bank1` behavior),
  holds PB7 BOOT0trig high for a measured 50 ms, then resets. USB flashing
  still writes 0x08000000 with `tools/dfu`; no dual-bank OTA implementation
  was copied. Physical BOOT0/reset and ST-Link remain recovery paths.

## Control commissioning

The inherited pedal calibration, map and event tables remain unchanged.
Confirm endpoints with `/cal`: the inherited upper deadzone maps 74% of
calibrated sensor travel to full pedal demand.

Four independent wheel speeds are subscribed on the **Data** CAN bus,
not GPIO: `0x402` FL, `0x403` FR, `0x404` RL and `0x405` RR. This matches
last year's USM sender and generated packer: bytes 6–7 are signed
little-endian counts at 0.01 rad/s. The actual sender period is 3 ms;
legacy `FREQ=3` means milliseconds, not 3 Hz. Each corner expires after two
periods. The older 0x130 packet cannot supply four independent speeds.
`/state` reports wheel validity and drive/braking slip.

`WheelControlParams::enabled` enables rear-drive slip control. It uses the
faster undriven front wheel as speed reference, the faster rear wheel to
detect one-wheel spin, and a bounded PI torque reduction. Reductions act
immediately; torque recovery is limited. Missing/negative wheel feedback
cuts positive torque while this feature is enabled. Near-zero speed uses a
bounded denominator. Both tire radii match `vehicle/vehicle.yml` (0.2045 m).
The 10% slip targets and controller gains are commissioning values; defaults
remain off pending tire/vehicle tuning. Front/rear geometry differences in
corners and front-wheel locking can bias this reference; it is not an ABS
or a validated vehicle-speed observer.

The inherited `TractionParams` motor-acceleration limiter remains available
as a fallback, disabled as last year. It uses CAN sample intervals, filtered
acceleration, held trim and a bounded integral. Enabling wheel-slip control
bypasses that fallback so two traction limiters do not fight each other.

`LaunchParams::enabled` defaults false. `/launch arm` requires acceleration
mode, DRIVE, the brake pressed, released accelerator, near-zero motor speed
and fresh stationary wheel feedback. Releasing the brake and applying
throttle starts a torque cap ramp; the pedal, power and wheel-slip limits
still win. Brake application, lost wheel data, faults, invalid timing or
timeout abort it; an abort holds zero torque until pedal release. The
initial 30 Nm / 100 Nm/s values require vehicle tuning. Tests exercise the
feature enabled through the app's optional immutable parameter set.

The pack is **132s3p Reliance INR21700-RS50**, confirmed by Rishi; the
VCU regen settings use this configuration without changing the shared vehicle model. RS50 end-of-charge
voltage is 4.2 V/cell, so the cell-based pack ceiling is 554.4 V. The inherited
4.05 V max-cell regen cutoff remains more conservative.

The [RS50 manufacturer product page](https://www.reliance-battery.com/products-en.html)
lists 5 Ah capacity and 3C charging performance; the manufacturer-authored
[datasheet](https://www.nkon.nl/amfile/file/download/file/504/product/5764/)
lists 15 A maximum continuous charging per cell. Multiplying by three parallel
cells gives a 45 A cell-based ceiling, not a verified pack/BMS permission.
The existing HVC charger setting of 9.5 A is a charger command, not a regen
charge-limit broadcast.

`RegenParams::enabled` remains false. The unverified charging-current limit,
hard-cut threshold and DC pack resistance default to zero, which prevents
isolation/regen even if only the enable flag is switched on. Set these from
validated pack/BMS limits and measured DC resistance before commissioning.
Do not reuse last year's 130s5p resistance or substitute datasheet 1 kHz AC
impedance for the DC model. Battery-temperature/charge-acceptance feedback
still needs connecting. Tests supply explicitly synthetic limits to exercise
the enabled algorithm without treating them as real pack settings.

The inherited pressure mapping remains 67 Nm per 500 psi, with a 200 ms
valve-command delay; both still require hydraulic/actuator validation.

Regen pre-requests isolation only while both rear pressures are at most
10 psi, the accelerator is released, speed/voltage/cell/BSE3 feedback is
fresh (including all four Data-bus wheel speeds), and neither hardware nor
firmware reports hard braking. It waits
for the VCU's gated output and the 200 ms command delay, then requests
negative torque proportional to pre-lock pressure minus post-lock pressure.
That subtraction compensates estimated remaining hydraulic rear braking.
Pack voltage/current and motor speed further limit torque. Hard braking,
stale feedback, rear braking slip above the configured threshold, low speed,
accelerator demand or a current cut releases
the request and removes negative torque. After fallback it cannot close
again under existing brake pressure. Positive torque is suppressed during
a regen braking demand, even if regen becomes unavailable.

`linelock_out` is the VCU logic gate, not physical valve acknowledgement.
The PDU still owns actuation over CAN; validate its response and stale-CAN
release behavior. Brake equivalence, transitions and rear-wheel stability
need bench/vehicle measurements before enabling regen. The slower rear wheel
is checked for locking relative to the front reference; this suppresses regen
and opens isolation, but cannot modulate hydraulic brake pressure like ABS.

CAN IDs, layouts, and buses are defined in [lib/spec](../../lib/spec/README.md)
(see the [CAN matrix](../../docs/can-matrix.md)). Critical-bus content keeps
the 2026 IDs and layouts. New diagnostics (`VCU_ADC_*`, `VCU_DIGITAL_IO`,
`VCU_DIAG`, `VCU_CALIBRATED_*`) are on the Data bus.

## Build, flash and check

There is one firmware image: `:vcu`. It starts the vehicle app, ADC/DAC,
both CAN buses, the RTOS tasks, watchdog supervision, both consoles and the
rainbow. The temporary MVP and bench firmware variants have been removed;
`vcu_test_app.*` remains as a host integration-test harness.

```bash
bazel build --config=local //boards/VCU:vcu
bazel run --config=local //boards/VCU:dfu
# Or, with an ST-Link attached:
bazel run --config=local //boards/VCU:openocd
bazel run --config=local //tools/monitor
```

After a manual BOOT0 flash, return the switch to normal boot and reset.
Verify `/version` says `Vehicle Control Unit`, `/uptime` increases, and
`/state`, `/faults`,
`/io`, `/analog` respond. USB-only operation without the car produces
missing-feedback/input faults; zero torque and disabled inverter commands
are expected. The vehicle image sends CAN traffic, including inverter
commands; use an isolated bench setup before connecting the drivetrain.
The vehicle shell deliberately has no manual output overrides.

Pedal plausibility, ADC progress, CAN freshness, shutdown-loop checks and
the watchdog remain enabled. Regen, launch ramp and traction/slip-control
features remain disabled until commissioning, as documented above; restoring
the full app does not bypass their unverified limits or enable them.
The corrected D35 blue/green mapping applies to this image.

## IOC gaps

Optional `.ioc` cleanups. Everything here already works: `Board/` (or a
`USER CODE` block) sets it up at runtime, and each workaround can go once
CubeMX generates the equivalent. Item 7 is cosmetic (`main.cpp` drives the lamps high right after
`MX_GPIO_Init`); item 8 is now resolved.

1. `ProjectManager.NoMain=true`. Today `Core/Src/main.c` renames its own
   `main()` away in `USER CODE BEGIN PFP`.
2. ADC1/ADC2 configuration:
   - TIM6 TRGO trigger;
   - DMA continuous requests;
   - 16× oversampling;
   - ADC1 ranks for IN4 and VREFINT, and ADC2 rank IN11 with DMA on
     DMA1_Ch2;
   - drop the unused AWD3.
3. DAC1 CH1 triggered by TIM7 TRGO, with DMA on DMA1_Ch3. TIM6/TIM7 as
   basic timers with TRGO on update.
4. Resolved: FDCAN2 (Critical) automatic retransmission and IT0/IT1 at
   priority 5 are now in `VCU.ioc`. CubeMX regenerated `Core/Src/fdcan.c`,
   `Core/Src/stm32g4xx_it.c` and `Core/Inc/stm32g4xx_it.h`; the Board
   register/NVIC fallback and duplicate handlers were removed together.
   No pending regeneration is needed for this item.
5. IWDG. Today the LHAL adapter drives it at register level.
6. USART3 on PC10/PC11 for the STDC14 VCP. Today `comms_setup.cpp` sets it
   up, and `BUILD.bazel` defines `HAL_UART_MODULE_ENABLED`.
7. GPIO user labels, and initial level high on PE12/PE13 (BMS/IMD lamps
   on).
8. Resolved: the IOC names the USB manufacturer `LHRe` and product/interface
   `Vehicle Control Unit`. The generated USB descriptor follows these settings.
9. FreeRTOS stack-overflow and malloc-failed hooks. Today they are set in
   the `FreeRTOSConfig.h` USER CODE.

The clock tree is unchanged from the 2026 VCU: 16 MHz HSE → 96 MHz SYSCLK,
FDCAN on PCLK1, ADC on HCLK/2 = 48 MHz, USB on PLLQ.

## Open items (verify on hardware)

- D35 required a 180° assembly rotation for correct polarity; rainbow operation
  was confirmed on the bench. Firmware swaps blue/green in all images for
  this reworked orientation; PCB net labels retain their original names.
- RTD switch type. The firmware leaves DRIVE when the switch opens, as the
  2026 toggle did (`DriveParams::rtd_release_exits_drive`).
- APPS and BSE sensor ranges and pedal endpoints. Defaults assume 0.5-4.5 V
  sensors; capture the real values with `/cal`.
- Steering mapping constants (carried from the 2026 sensor), and the
  hard-brake trimmer set point (`BseParams::hard_brake_pin_v`).
- Polarity of `motor_disconnect`. Neither motor cable has a pull resistor,
  so an unplugged cable reads undefined. `motor_power` remains diagnostic;
  `motor_disconnect` now cuts torque when high after debounce.
- Rule text for every threshold marked `TODO(verify rule)`. The RTD sound
  needs measuring against its 80-90 dBA band.

### Shared services and board adapters

The shell, debounce/filter helpers, scheduler lock and CAN inbox/RX slots
come from [longhorn](../../drivers/longhorn/README.md). USB uses
`lhal::stm32::UsbCdc` and its receive-path observer for recovery detection;
the generated CDC interface source remains excluded by `firmware_project`.
`Board/main.cpp` constructs the adapter before `MX_USB_Device_Init`.
The VCU retains its ADC progress interface, host ADC/UART fakes, legacy RGB
brightness and rainbow task. `Board/dma_adc.hpp` supplies the ADC adapter.

Vehicle task creation and host scheduling stay in `vcu_app.cpp`. Control and
snapshot publication live in `vcu_control.cpp`, Critical-bus TX and pure
message builders in `vcu_critical_tx.cpp`, diagnostics in `vcu_telemetry.cpp`,
watchdog/recovery in `vcu_supervisor.cpp`, and console commands in
`vcu_shell.cpp`. These remain private `App` operations with the same task
ownership and send ordering. Rate gates use longhorn's checked `PeriodMs`.

`VehicleRx` and `WheelRx` extend the generated VCU Critical/Data subscription
structs from `:can_rx_vcu`; only vehicle freshness/fault policy stays local.
Subscriptions come from the existing spec `to_boards` fields.

## USB serial monitor

USB enumerates as **Vehicle Control Unit** (manufacturer **LHRe**), with
unchanged VID:PID and hardware serial number. The port path is assigned by
the host OS; its friendly USB product name is separate. These strings come
from `VCU.ioc` and CubeMX regeneration of `USB_Device/App/usbd_desc.c`.
The ROM bootloader retains ST's own DFU name.

Opening the USB serial port prints the `Vehicle Control Unit` firmware
banner, reset cause and command hint, even when the monitor connects after
boot. Every second it prints state, fault mask, ADC freshness, pedal demand,
torque/enable, APPS voltages, front/rear brake pressure and motor RPM.
Status output is always enabled while the USB port is open; no stream
command or setup is needed. The banner and status return on reconnect.
`/state`, `/faults`, `/io` and `/analog` still give detailed on-demand output.
Serial writes run only in the low-priority USB shell task, using a published
snapshot; the control loop does not wait for the monitor. Existing event
logging remains on the debug USART3 console.

On macOS select `/dev/cu.usbmodem…` in external serial monitors. The friendly
USB name does not rename this device path. Only one monitor should own the
port at a time; close an existing monitor before opening another. The repo
monitor selects the `cu` port automatically:

```bash
bazel run --config=local //tools/monitor
```
