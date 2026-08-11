# VS Code on-target debugging

Press F5 in VS Code with an ST-Link plugged in and you get breakpoints,
stepping, variable/register inspection, FreeRTOS thread views, live watch,
and the SVD peripheral viewer — on any OS, with nothing to install beyond
VS Code itself and the [Cortex-Debug] extension (recommended automatically
via `.vscode/extensions.json`).

[Cortex-Debug]: https://marketplace.visualstudio.com/items?itemName=marus25.cortex-debug

## Quick start

1. Install the **Cortex-Debug** extension (VS Code will offer it as a
   workspace recommendation; it pulls in the mcu-debug peripheral/RTOS/
   memory viewers as dependencies).
2. Plug in the board over ST-Link.
3. Run and Debug panel → **Debug VCU (ST-Link)** → F5.

The launch builds the firmware, flashes it, resets the MCU, and stops at
`main()`. Use **Attach VCU (ST-Link)** instead to inspect a board that is
already running without reflashing or resetting it (make sure it is
running the same commit, or line numbers will lie to you).

## How it works — and why it is hermetic

Cortex-Debug needs two executables: a gdb client and a gdb server. Both
already exist in the Bazel workspace — Arm's `arm-none-eabi-gdb` ships in
the same hermetic toolchain archives the firmware compiles with (including
a native darwin-arm64 build, so Apple Silicon needs no Rosetta and no
separate gdb install), and the xPack OpenOCD used by the
`//boards/*:openocd` flash targets includes a gdb server.

The catch is that Bazel fetches those into the output base, whose absolute
path differs on every machine, while `.vscode/launch.json` needs paths it
can check in. The two `copy_to_directory` targets in this package bridge
that: they stage the host's gdb and a self-contained OpenOCD tree at
stable workspace-relative paths —

```text
bazel-bin/tools/debug/gdb/bin/arm-none-eabi-gdb[.exe]
bazel-bin/tools/debug/openocd/bin/openocd[.exe]
bazel-bin/tools/debug/openocd/openocd/scripts/     (TCL script library)
```

— and the checked-in `launch.json` points there (with a `windows:`
override for the `.exe` suffixes). The `build-vcu-debug` task in
`.vscode/tasks.json` keeps them and the firmware ELF fresh before every
launch, so the first F5 on a clean checkout just works.

The pieces the launch config wires together:

- **ELF**: `bazel-bin/boards/VCU/vcu.elf`. The default build already
  compiles `-Og -g3`, and `.bazelrc` sets `--strip=never` because Bazel's
  fastbuild default (`--strip=sometimes`) would silently discard the DWARF
  at link time. Debug info lives only in the ELF; the `.bin`/`.hex` images
  are unaffected.
- **OpenOCD config**: the same `drivers/stm32/stm32g4/stm32g4_openocd.cfg`
  the flash target uses (ST-Link interface + STM32G4 target).
- **SVD**: `boards/VCU/STM32G474.svd` (Apache-2.0, from ST's CMSIS-SVD
  pack) drives the peripheral-register viewer.

## Adding a board

Per board: stage its `STM32xxxx.svd` next to its `BUILD.bazel`, then copy
the two configurations in `.vscode/launch.json`, changing `executable`,
`svdFile`, `device`, and the family's OpenOCD `configFiles` entry. Per
family, `drivers/stm32/<family>/` needs an OpenOCD cfg (see the G4 one).

## Troubleshooting

- *"Unable to match requested speed"* in the OpenOCD log is normal — the
  adapter negotiates down and everything works.
- OpenOCD exits with *"no device found"*: the ST-Link is not enumerating
  (check the cable/port), or on Linux you are missing udev rules — install
  [OpenOCD's udev rules](https://openocd.org/doc/html/Running.html) or
  your distro's `openocd` package once to get them.
- Paths under `bazel-bin/` are dangling: run the **build-vcu-debug** task
  (or any `bazel build` of `//tools/debug:debug_tools`) to restage.
- Debugging works without a GitHub connection: only the shared build
  cache needs the network, and it falls back to building locally
  (`--config=offline` skips it entirely).
