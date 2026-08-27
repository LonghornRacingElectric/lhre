# Platforms

One Bazel target platform per STM32 family (`:stm32f0`, `:stm32f4`,
`:stm32g4`, `:stm32h7`). Each is `arm` + `os:none` plus an `:mcu_core`
constraint (`cortex_m0` / `cortex_m4f` / `cortex_m7f`) that selects the
matching [//toolchains](../toolchains/README.md) variant, where the family's
`-mcpu`/`-mfpu`/`-mfloat-abi` live.

`mcu_core` is a custom constraint setting because `@platforms//cpu` is too
coarse: cortex-m4f and cortex-m7f are both armv7e-m with hard float, yet
need different `-mcpu`/`-mfpu` flags.

Nobody passes `--platforms` by hand: `firmware_project` transitions each
board to its family's platform automatically (see
[tools/firmware](../tools/firmware/README.md)).

## Adding a family

1. If it's a new CPU core, add the `constraint_value` here and a toolchain
   variant in [//toolchains](../toolchains/README.md).
2. Add the `platform` here with that core constraint.
3. Map the family in `FAMILY_PLATFORMS`
   (`//tools/firmware:firmware_project.bzl`) and package its HAL in
   [//drivers/stm32](../drivers/stm32/README.md).

## Not a firmware platform: `host_no_remote_exec`

`//platforms:host_no_remote_exec` is the auto-detected host platform plus a
`no-remote-exec` property. `.bazelrc` sets it as `--host_platform` wherever
remote execution is on, so actions that resolve to the *host* exec platform
(e.g. rules_rust building host-targeted crates and their bootstrap tools)
run locally instead of being shipped to the Linux executors, where host
(macOS/Windows) binaries can't run. C++ never needs this — hermetic LLVM
resolves to the Linux exec platform and cross-compiles.
