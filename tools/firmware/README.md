# firmware_project

The macro every board's `BUILD.bazel` calls to turn sources into flashable
firmware. One call produces the whole target family (shown for
`firmware_project(name = "vcu", …)` in `//boards/VCU`):

| Target                              | What it is                                             |
| ----------------------------------- | ------------------------------------------------------ |
| `:vcu`                              | The linked firmware (ELF), built for the family's MCU. |
| `:vcu.elf` / `:vcu.hex` / `:vcu.bin` | objcopy'd output formats.                              |
| `:vcu.out.map`                      | Linker map (`--cref`), for section/size spelunking.    |
| `:openocd`                          | `bazel run` target: flash the ELF over ST-Link.        |
| `:dfu`                              | `bazel run` target: flash the BIN over USB DFU.        |
| `:release`                          | Filegroup of every elf/bin/hex (for CI artifacts).     |

## How the MCU flags get there

The macro never passes `-mcpu`/`-mfpu`/`-mfloat-abi` anywhere. `family`
(e.g. `"stm32g4"`) maps to a platform in [//platforms](../../platforms/README.md)
whose `mcu_core` constraint selects the matching
[//toolchains](../../toolchains/README.md) variant with those flags baked in.
The public `:vcu` target is a `platform_transition_filegroup`, so a plain
`bazel build //boards/VCU:vcu` cross-compiles correctly with no `--platforms`
flag — and every `cc_library` in the dependency graph (app logic, LHAL, HAL)
gets the same codegen flags, not just the final binary.

Adding a family = adding it in those two packages, then mapping it in
`FAMILY_PLATFORMS` here. The macro fails loudly on unknown families.

## Options worth knowing

- `locations = ["FR", "FL"]` builds one image per position on the car:
  targets become `:vcu_FR` / `:openocd_FR` / …, each compiled with a
  `BOARD_FR` define so one codebase serves several corners.
- `enable_freertos` compiles the FreeRTOS kernel + the family's Cortex-M
  port into the firmware (from `//drivers/stm32/<family>:freertos_srcs`; the
  kernel is pinned once in [drivers/freertos](../../drivers/freertos/README.md)).
  It also wires in the shared CubeMX glue automatically — the
  SysTick→`xPortSysTickHandler()` forwarding handler
  (`//drivers/freertos:cubemx_glue`) and a stub `cmsis_os.h`
  (`//drivers/freertos:cmsis_os_stub`) that satisfies the include in the
  CubeMX-generated `main.c`, so boards write neither. The board owes two
  things in return, both covered by enabling FreeRTOS in the `.ioc` (see
  [boards/VCU](../../boards/VCU/README.md) for the worked example):
    1. `Core/Inc/FreeRTOSConfig.h` — CubeMX-generated; the kernel sources
       compile against it.
    2. `exclude = ["Core/Src/app_freertos.c"]` in the srcs glob — that file
       is CubeMX's CMSIS-RTOS2 glue and we use the raw FreeRTOS API.
- `enable_usb` wires in USB CDC (virtual COM port): ST's USB Device
  middleware (`//drivers/stm32/usb_device`, pinned once, family-independent)
  plus the board's CubeMX-generated `USB_DEVICE/` glue — except
  `usbd_cdc_if.c`, which is deliberately not compiled: its only content is
  the CDC callback struct (`USBD_Interface_fops_FS`), and
  `lhal/stm32/usb_cdc.cpp` defines that instead so reception routes into
  `lhal::stm32::UsbCdc` (an `lhal::Uart`). The board owes: USB_Device (CDC)
  enabled in the `.ioc` so `USB_DEVICE/` exists, and a
  `static lhal::stm32::UsbCdc` constructed before `MX_USB_Device_Init()`.
- `enable_dfu` defines `ENABLE_DFU` — firmware that listens for the
  `update.` serial command enables probe-less reflashing (see
  [tools/dfu](../dfu/README.md)).
- `enable_printf_float` links `-u _printf_float` (~10 KB of flash).
- `driver_headers` / `driver_srcs` override the default
  `//drivers/stm32/<family>` HAL — for a board on a family we haven't
  packaged yet.

Flash targets are tagged `local` (they must run where the board is plugged
in, never on remote executors).

## Build provenance

Every firmware binary automatically links `//tools/firmware:build_info`:

```cpp
#include "lhre/build_info.hpp"
// lhre::kBuildInfo.git_describe, .git_sha, .dirty
```

The pipeline: the workspace-status scripts (`tools/workspace_status.sh`/`.bat`)
emit `STABLE_GIT_*` keys → the `build_info_header` rule (`build_info.bzl`)
runs `gen_build_info.py` to render them into `lhre/build_info.hpp`. It's a
custom rule rather than a genrule so it works shell-free on Windows clients
and remote Linux executors alike, and it depends only on *stable* status —
the header (and everything linking it) rebuilds when the commit, tag, or
dirty state changes, never on timestamps.

To add a provenance field (e.g. a CAN spec hash), follow the recipe in
`gen_build_info.py`'s docstring.
