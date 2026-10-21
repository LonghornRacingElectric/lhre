# DFU flashing

Reflash a board over plain USB — no debug probe:

```bash
bazel run //boards/VCU:dfu
```

`dfu.bzl` fetches pinned dfu-util 0.11 binaries for the host OS as `@dfu`
(with a Firebase mirror, since SourceForge likes to flake). `flash.py` is
the `bazel run` entry point (wrapped per-board by `dfu_flash_target` in
[tools/firmware](../firmware/README.md)). The flow:

1. Scan serial ports for the running firmware (description containing
   `lhre`, VID:PID `0483:5740`, or Windows' generic "USB Serial Device")
   and send it `update.\n` — firmware built with `enable_dfu = True`
   reboots into the ST system bootloader on that command.
2. Find the DFU device (`0483:df11`) and download the `.bin` to flash at
   `0x08000000`, then `:leave` to boot the new image.

If no serial port matches (device already in DFU mode, e.g. via the BOOT0
pin), step 1 is skipped and it flashes directly.

For a local build without BuildBuddy credentials, use
`bazel run --config=local //boards/VCU:dfu_test` for the VCU bring-up image
(or `:dfu` for the vehicle image). A successful build does not mean a flash
succeeded: the script must also find the ROM DFU device. If it cannot find
`0483:df11`, it exits before writing flash. For a blank board, enter ROM
DFU using the board's BOOT0/reset controls and a USB data cable; the running
firmware's `update.` command is unavailable until firmware has been installed.
