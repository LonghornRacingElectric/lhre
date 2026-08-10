# VCU

Vehicle Control Unit firmware (STM32G474) — and the reference board layout
for the repo.

## Targets

| Target          | What                                                        |
| --------------- | ----------------------------------------------------------- |
| `:vcu`          | Firmware ELF (plus `:vcu.elf` / `:vcu.hex` / `:vcu.bin`).   |
| `:openocd`      | `bazel run` — flash over ST-Link.                           |
| `:dfu`          | `bazel run` — flash over USB DFU.                           |
| `:vcu_app_test` | App logic under googletest against LHAL host fakes.         |
| `:vcu_sim`      | Interactive host sim (`bazel run --config=local`).          |
| `:release`      | elf/bin/hex bundle for CI artifacts.                        |

## Layout

- `App/` — application logic (`vcu::App`), a plain `cc_library` depending
  only on `//drivers/lhal`. The same code links into the firmware, the
  host test, and the sim; see the
  [LHAL README](../../drivers/lhal/README.md) for the pattern.
- `Board/` — hand-written bring-up: `main.cpp` configures clocks, pins, and
  peripheral handles at the ST HAL level, wraps them in LHAL adapters, and
  hands them to the app. Owned by us; formatted normally.
- `Core/` — CubeMX-generated from `VCU.ioc`. Regenerate via CubeMX, then run
  `./post_cubemx.sh` (deletes the generated `main.c`; our entry point is
  `Board/main.cpp`). Never hand-edit.
- `STM32G474XX_FLASH.ld` / `startup_stm32g474xx.s` — linker script and
  startup for the G474, passed to `firmware_project`.

The `BUILD.bazel` here is the canonical example of a `firmware_project`
call — what it generates and every option is documented in
[tools/firmware](../../tools/firmware/README.md).
