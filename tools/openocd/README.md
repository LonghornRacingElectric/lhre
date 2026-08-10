# OpenOCD flashing

Flash a board over an ST-Link (or any OpenOCD-supported probe):

```bash
bazel run //boards/VCU:openocd
```

No install needed: `openocd.bzl` is a module extension that fetches the
xPack OpenOCD build for your host OS/arch as `@openocd`, so every machine
flashes with the same pinned version.

`flash.py` is the `bazel run` entry point (wrapped per-board by
`openocd_flash_target` in
[tools/firmware](../firmware/README.md)). It resolves the OpenOCD binary,
the board's ELF, and the family's config file
(`//drivers/stm32/<family>:openocd_cfg`) out of runfiles, then runs
`program <elf> verify reset exit`.

Requires a debug probe wired to SWD. For reflashing over USB alone, see
[tools/dfu](../dfu/README.md).
