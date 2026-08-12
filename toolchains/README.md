# ARM toolchains

(Also here, unrelated to ARM: [proto/](proto/) registers the Python proto
toolchain that pairs the prebuilt `protoc` with the pip protobuf runtime —
the reason protobuf is never compiled from source; see
[docs/build-system.md](../docs/build-system.md).)

arm-none-eabi GCC 13.2.1 `cc_toolchain`s, one variant per CPU core we ship
firmware on, with the core's codegen flags baked in:

| Variant      | Flags                                          | Used by      |
| ------------ | ---------------------------------------------- | ------------ |
| `cortex_m0`  | `-mcpu=cortex-m0 -mfloat-abi=soft`             | stm32f0      |
| `cortex_m4f` | `-mcpu=cortex-m4 -mfpu=fpv4-sp-d16` hard float | stm32f4/g4   |
| `cortex_m7f` | `-mcpu=cortex-m7 -mfpu=fpv5-d16` hard float    | stm32h7      |

The upstream `@arm_none_eabi` toolchains carry no codegen flags — that's the
point of this package. Baking `-mcpu`/`-mfpu`/`-mfloat-abi` into the
*toolchain* (selected via the target platform's
[`//platforms:mcu_core`](../platforms/README.md) constraint) means every
`cc_library` in the dependency graph compiles with the right float ABI, with
zero per-target flag plumbing. The flags sit in both copts and linkopts:
linkopts also drive newlib/libgcc multilib selection.

Optimization/debug level lives here too, keyed on `--compilation_mode`:
`-Og -g3` by default, `-Os -g3` under `-c opt` (CubeMX's Release level; see
`MODE_COPTS` in `BUILD.bazel`). In the toolchain rather than in
`firmware_project` copts because a target-level `-O` flag would override
`-c opt` and quietly make release builds impossible.

`toolchain.bzl` is a local mirror of the fork's `arm_none_eabi_toolchain`
macro with one change: the generated `cc_toolchain`/config targets are tagged
`manual`. Without that, `bazel build //...` matches the toolchains for all
five host OS/arch combos and downloads every host's ~150 MB gcc archive.
Toolchain *resolution* ignores tags, so the registered `toolchain()`
declarations still work and only the selected variant fetches its repo.

## Adding a variant

New CPU core (say cortex-m33): add a `constraint_value` in `//platforms`,
an `arm_none_eabi_toolchain(...)` call in `BUILD.bazel` here with the core's
flags, and use the constraint in the new family's platform. `//toolchains:all`
is already registered in `MODULE.bazel`.
