# DUI

Driver User Interface firmware (STM32G474CET6, LQFP48). The DUI reads the
steering-wheel buttons and switches and the E-Stop / Inertia sense lines,
forwards them over CAN, and powers the dash screen. Screen logic lives on
BEVO, not here.

**Status: Rev A bring-up.**

- **LED:** white lamp test at power-up, then a smooth rainbow (test plan
  DUI-003 / DUI-F-001).
- **Inputs:** the 2 driver buttons and 4 steering switches (10 lines) are
  sampled at 1 kHz and debounced (stable > 5 ms). Every change is logged to
  the USB console, and `/inputs` prints the current state (DUI-007,
  DUI-F-002/F-003). A switch reading UP and DOWN at once logs an error.

Shutdown sense, CAN forwarding, and heartbeats come next.

## Console

Plug in USB-C (SW3 off, so the firmware runs) and:

```bash
bazel run //tools/monitor
```

```text
[1520] [INFO] BTN1 pressed
[1610] [INFO] BTN1 released
[3002] [INFO] TOP_L_UP pressed
> /inputs
BTN1=0 BTN2=0
TOP_L=UP
...
```

Commands: `/inputs`, plus the shell built-ins `/help`, `/version`,
`/uptime`. Log lines are only produced while a terminal has the port open.
`enable_dfu = True` in `BUILD.bazel` also wires `update.\n` (used by
`bazel run //boards/DUI:dfu`) to drive `PB9` (`BOOT0trig`) high for 50 ms
and reset into the ST ROM bootloader.

## Targets

| Target          | What                                                     |
| --------------- | -------------------------------------------------------- |
| `:dui`          | Firmware ELF (plus `:dui.elf` / `:dui.hex` / `:dui.bin`). |
| `:openocd`      | `bazel run`: flash over ST-Link (STDC14 header J3).       |
| `:dfu`          | `bazel run`: flash over USB DFU.                          |
| `:dui_app_test` | Host tests: LED (lamp test, `longhorn::RgbLed` rainbow, active-low) and console (input logs, `/inputs`, fault check). |
| `:driver_inputs_test` | Host tests: debounce timing, bounce, glitches, clock wrap. |
| `:dui_sim`      | Host sim running the real tasks: scripted button presses, printed as the console would show them (`bazel run --config=local`). |
| `:release`      | elf/bin/hex bundle for CI artifacts.                      |

## Layout

Same split as [VCU](../VCU/README.md), the reference board:

- `App/`: application logic against LHAL plus the raw FreeRTOS API. Runs
  unchanged in the host tests and sim.
  - `driver_inputs.*` debounces the 10 input lines. `Input`'s order is the
    planned CAN bit order, so append only.
  - `dui_app.*` owns the tasks (inputs 1 kHz, LED 100 Hz, shell, logger
    drain), the `/inputs` command, and drives `longhorn::RgbLed`.
- `Board/main.cpp`: bring-up. Calls the CubeMX inits (`MX_TIM2_Init`,
  `MX_USB_Device_Init`), configures the input pins, wraps everything in
  LHAL adapters, starts the scheduler.
- `Core/`, `USB_Device/`: CubeMX-generated from `DUI.ioc`. Never hand-edit.

## Pins in use

From the Rev A schematic (`DUI.kicad_sch`):

| Pin       | Net     | Use                                         |
| --------- | ------- | ------------------------------------------- |
| PA1       | LED_R   | RGB LED red, TIM2_CH2 PWM, active-low        |
| PA0       | LED_G   | RGB LED green, TIM2_CH1 PWM, active-low (see below) |
| PA2       | LED_B   | RGB LED blue, TIM2_CH3 PWM, active-low (see below)  |
| PB15 / PB14 | Driver_Button_1 / 2 | SW1 / SW2 push buttons, input  |
| PB2 / PA15 | Switch_Top_L_UP / DOWN | SW5, input                  |
| PB0 / PB1 | Switch_Top_R_UP / DOWN | SW8, input                   |
| PA6 / PA5 | Switch_Bottom_L_UP / DOWN | SW6, input                |
| PA8 / PA7 | Switch_Bottom_R_UP / DOWN | SW7, input                |
| PA9/PA10  | VCP_TX/RX | USART1 to the STDC14 virtual COM port      |
| PA11/PA12 | USB_DN/DP | USB-C: CDC debug console, and DFU flashing |
| PB12/PB13 | CAN_RX/TX | FDCAN2, not brought up yet                 |
| PB11      | STB     | CAN transceiver standby, not brought up yet  |
| PB8/PB9   | BOOT0 / BOOT0trig | Firmware-triggered bootloader entry |

## Gotchas

- **The RGB LED is common-anode.** Its anode is on Vdd, so a channel is lit
  while its pin is LOW. TIM2 keeps CubeMX's default active-high polarity and
  `RgbLed` inverts in software (brightness b → duty 1 − b). `main.cpp`
  builds the app, which sets every channel off, before starting the PWM
  outputs; a channel started at the default 0% duty would be fully lit.
- **TIM2 in `DUI.ioc`:** internal clock, Channels 1–3 = PWM Generation,
  Prescaler 15 and Counter Period 999 (1 kHz PWM from the 16 MHz HSI),
  everything else default.
- **Rev A: D11's footprint doesn't match the fitted part (DUI-BUG-001).**
  The symbol and footprint assume a common-anode LED (anodes on pins 1-3 to
  Vdd, cathodes on 4-6 to the resistors), but the part has three
  independent dies: blue 1(−)/2(+), red 3(−)/4(+), green 5(−)/6(+). Blue
  ends up shorted, red reverse-biased, green between two MCU pins, so the
  on-board LED stays dark. The firmware is fine: PA0-PA2 toggle 0-3.3 V on
  a meter. Rotating D11 does **not** fix it (red would then sit on a 22 Ω
  resistor, ~60 mA). For Rev B, tie anodes 2/4/6 to Vdd and route cathodes
  3 (R) → R23, 5 (G) → R25, 1 (B) → R21, or fit a true common-anode part.
  To see the rainbow on Rev A, bodge an LED (anode to 3.3 V, ~330 Ω in
  series) to the MCU end of each resistor.
- The rainbow is gamma-corrected (`RgbLed::kGamma`, 2.2): duty is
  brightness^2.2, so fades look even to the eye instead of jumping to
  bright.
- **Inputs are active-high with external pull-downs** (10 kΩ + 100 nF per
  line on the Switches sheet), so the pins are configured `GPIO_NOPULL` in
  `main.cpp`. That matters for **PA15**, which comes out of reset as JTDI
  with an internal pull-up that would otherwise hold Top-L DOWN "pressed".
- **CubeMX renames the linker script on every Generate Code.** It writes
  `STM32G474xx_FLASH.ld`, but `firmware_project` expects
  `STM32G474XX_FLASH.ld` (and Linux CI is case-sensitive). After
  regenerating, rename it back. On macOS that takes two `mv`s via a temp
  name.
- **USB clock:** CubeMX derives USB's 48 MHz from the PLL fed by HSI16
  (16 × 12 / 4); the CPU stays on HSI16. USB full-speed wants ±0.25%, and
  HSI16 is only trimmed to about ±1% over temperature, so if enumeration
  gets flaky, switch USB to HSI48 with CRS synced to USB SOF in `DUI.ioc`.
- **Don't copy VCU's debug console pins.** VCU's shell runs on LPUART1 at
  PA2/PA3, but PA2 is LED_B here. The DUI's console is USB CDC; USART1 on
  PA9/PA10 (the STDC14 VCP) is free for a second console with an ST-Link.
- The clock runs on HSI (16 MHz), like VCU. The board has an HSE crystal
  on PF0/PF1; switch to it before bringing up CAN, which needs an accurate
  bit clock.
