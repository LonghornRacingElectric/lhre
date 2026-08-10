# Vendor HAL packages

ST's HAL and CMSIS, packaged for Bazel — one subpackage per STM32 family
(currently `stm32g4/`), plus the family-independent USB Device middleware
in [usb_device/](usb_device/README.md) (`:headers` / `:srcs`, same
compile-inside-the-firmware-binary pattern). Boards never reference the
external repos directly; each family exposes a stable label surface that
`firmware_project` wires in by family name:

| Target                       | Contents                                        |
| ---------------------------- | ----------------------------------------------- |
| `:headers`                   | HAL + CMSIS device + CMSIS core headers.        |
| `:srcs`                      | HAL driver `.c` sources (templates excluded).   |
| `:freertos_srcs` / `:freertos_headers` | FreeRTOS kernel + the family's Cortex-M port + `heap_4` + static-allocation hooks. The kernel itself is pinned in [drivers/freertos](../freertos/README.md), shared with host tests. |
| `:openocd_cfg`               | OpenOCD config for the family's flash/target.   |

The sources come from pinned upstream commits: each family's `deps.bzl` is a
module extension declaring `git_repository`s for the ST/ARM/FreeRTOS repos,
overlaid with our `*.BUILD` files (upstream ships no Bazel build files).
`MODULE.bazel` instantiates the extension and `use_repo`s the results.

The HAL compiles as *sources inside each firmware binary* (a filegroup, not
a `cc_library`) deliberately: HAL `.c` files include the board's
`stm32g4xx_hal_conf.h` and device define, which differ per board.

## Adding a family

Copy the `stm32g4/` package: new `deps.bzl` pinning that family's
`stm32<fam>xx_hal_driver` / `cmsis-device-<fam>` commits, `*.BUILD` overlay
files, a `BUILD.bazel` re-exporting the same target names, and an OpenOCD
cfg. Register the extension in `MODULE.bazel`, then add the family's
platform ([//platforms](../../platforms/README.md)) and `FAMILY_PLATFORMS`
entry.
