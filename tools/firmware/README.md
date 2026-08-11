# firmware_project

The macro every board's `BUILD.bazel` calls to turn sources into flashable
firmware. The design goal: a board states only what is genuinely its own —
everything derivable from the MCU or from the CubeMX reference layout is
derived, so there is nothing to copy-paste wrong into board #2. The whole
call for the VCU is:

```python
firmware_project(
    name = "vcu",
    enable_freertos = True,
    enable_printf_float = True,
    enable_usb = True,
    mcu = "stm32g474xx",
)
```

One call produces the whole target family:

| Target                              | What it is                                             |
| ----------------------------------- | ------------------------------------------------------ |
| `:vcu`                              | The linked firmware (ELF), built for the family's MCU. |
| `:vcu.elf` / `:vcu.hex` / `:vcu.bin` | objcopy'd output formats.                              |
| `:vcu.out.map`                      | Linker map (`--cref`), for section/size spelunking.    |
| `:openocd`                          | `bazel run` target: flash the ELF over ST-Link.        |
| `:dfu`                              | `bazel run` target: flash the BIN over USB DFU.        |
| `:release`                          | Filegroup of every elf/bin/hex (for CI artifacts).     |
| `:vcu_app`                          | The app library, synthesized from `App/` (see below).  |
| `:vcu_app_test` / `:vcu_rtos_test` / `:vcu_sim` | One host test per `App/*_test.cpp`, one host binary per `App/*_sim.cpp`. |

## One fact: `mcu`

Four things must agree for a board to boot — the toolchain family, the
device define, the linker script, and the startup file — and a mismatched
set compiles fine while producing broken firmware. So the macro takes the
one fact (`mcu = "stm32g474xx"`, ST's lowercase header spelling) and derives
the rest by ST's naming convention:

| Derived                | From `"stm32g474xx"`      |
| ---------------------- | ------------------------- |
| family (→ platform/toolchain, driver package) | `stm32g4` |
| device define          | `STM32G474xx`             |
| linker script          | `STM32G474XX_FLASH.ld`    |
| startup file           | `startup_stm32g474xx.s`   |

`linker_script` / `startup_script` / `driver_headers` / `driver_srcs`
override the convention for the odd board that deviates.

## The macro owns the CubeMX layout

Facts about CubeMX's output are CubeMX contracts, not board decisions, so
they live here once instead of in every board file:

- `Core/` (generated) and `Board/` (hand-written bring-up) are globbed by
  the macro. A board's `srcs` is additive — most boards pass nothing.
- With FreeRTOS, `Core/Src/app_freertos.c` is excluded: it's CubeMX's
  CMSIS-RTOS2 glue (`defaultTask` and friends), and boards use the raw
  FreeRTOS API instead.
- With USB, the generated `USB_DEVICE/` glue is compiled minus
  `usbd_cdc_if.c` (see `enable_usb` below).
- The [LHAL](../../drivers/lhal/README.md) STM32 backend
  (`//drivers/lhal:stm32_srcs` + `:stm32_headers`) is compiled into every
  firmware binary — it's part of the platform layer, and its sources guard
  themselves against missing optional middleware.

## App/ becomes targets by file name

Every board's app plumbing has the same shape, so the macro synthesizes it
from the `App/` directory instead of boards restating it:

- `App/**/*.cpp` / `*.hpp` (minus tests/sims) → `cc_library {name}_app`:
  LHAL interfaces + raw FreeRTOS API, linked into the firmware and built
  for the host. With `enable_freertos` it gets the host/MCU kernel split
  automatically — Cortex-M port + the board's CubeMX `FreeRTOSConfig.h`
  (exposed as `{name}_freertos_config`) when cross-compiling, simulator
  port + host config otherwise — so task code runs unmodified on the host.
- `App/*_test.cpp` → one small `cc_test` per file, named by its stem,
  against `{name}_app` + the LHAL host fakes + gtest.
- `App/*_sim.cpp` → one host `cc_binary` per file, named by its stem.

`app_deps` adds host-buildable dependencies to the app library (e.g.
`//drivers/longhorn`). A board that outgrows the convention sets
`enable_app = False` and hand-writes its app targets — passing the app
library via `extra_deps` — without giving up the firmware half.

## App code can't reach the ST HAL — by construction

The repo rule that app code depends on `//drivers/lhal` interfaces only
(never ST HAL — that's what keeps it host-testable) is enforced by
visibility, not by review: `//drivers/stm32/...` and the
`//drivers/freertos` internals are visible only to the LHAL backend and to
this package. A direct HAL dep from board or app code fails to build with
the offending label in the error.

That works because `firmware_project` is two macros: a legacy wrapper (the
board-facing API, which can `glob()` the board package) around a *symbolic*
macro that declares the `cc_binary` — Bazel checks the symbolic macro's
wiring against `//tools/firmware`, where it's defined, not against the
board package that called it. See the comment block in
`firmware_project.bzl`.

## How the MCU flags get there

The macro never passes `-mcpu`/`-mfpu`/`-mfloat-abi` anywhere. The family
derived from `mcu` maps to a platform in [//platforms](../../platforms/README.md)
whose `mcu_core` constraint selects the matching
[//toolchains](../../toolchains/README.md) variant with those flags baked in.
The public `:vcu` target is a `platform_transition_filegroup`, so a plain
`bazel build //boards/VCU:vcu` cross-compiles correctly with no `--platforms`
flag — and every `cc_library` in the dependency graph (app logic, LHAL, HAL)
gets the same codegen flags, not just the final binary.

Optimization level is also the toolchain's job, keyed on
`--compilation_mode`: default/`-c dbg` builds are `-Og -g3`, `-c opt` builds
are `-Os -g3` (CubeMX's own Release level). Nothing here hardcodes an `-O`
flag, so `bazel build -c opt //boards/VCU:vcu` really is the release build.

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
  CubeMX-generated `main.c`, so boards write neither. The board owes one
  thing in return, covered by enabling FreeRTOS in the `.ioc` (see
  [boards/VCU](../../boards/VCU/README.md) for the worked example):
  `Core/Inc/FreeRTOSConfig.h`, which the kernel sources compile against.
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
