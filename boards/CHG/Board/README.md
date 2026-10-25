# CHG hardware bring-up — pending

Known MCU: **STM32G474CET6**. No VCU pin map, clock tree, linker file, generated
Core code or USB/debug configuration is copied into this project.

Before adding a firmware entry point:

1. Confirm the PCB schematic, clock source, power/isolation domains and debug pins.
2. Select a dedicated display UART, 115200 8N1, compatible 3.3 V logic. Match the
   display's RX GPIO32 and TX GPIO25 through the designed low-voltage interface.
3. Allocate UART RX buffering/interrupt or DMA resources and scheduler cadence.
4. Define BMS/charger communications, current/voltage/temperature sensing, the
   independent shutdown path, contactor feedback and approved battery profiles.
5. Generate `CHG.ioc` and its `Core/`, startup and linker files with CubeMX. Use
   the repo scaffolder, then wire LHAL adapters and the real controller callbacks.

Never hand-edit generated `Core/` files or invent pin/clock settings from the
VCU. See the [CHG README](../README.md) for the transition from host infrastructure
to a generated firmware target.
