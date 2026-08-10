# LHRe

Longhorn Racing Electric's monorepo: firmware for every ECU on the car, the
hardware abstraction layer it's built on, and the tooling to build, test, and
flash it all with Bazel.

## Layout

| Directory  | What lives there |
| ---------- | ---------------- |
| [`drivers/`](drivers/README.md) | Hardware. [LHAL](drivers/lhal/README.md) (our platform-independent HAL) and [vendor HAL packages](drivers/stm32/README.md). |
| [`lib/`](lib/README.md)     | Platform-agnostic C++ that builds on host **and** target (ring buffers, CRC, CAN pack/unpack, …), with colocated tests. |
| [`apps/`](apps/README.md)    | Host-side software: telemetry, dashboards, gateways. |
| [`boards/`](boards/README.md)  | Per-ECU firmware projects ([`boards/VCU`](boards/VCU/README.md) is the reference layout). |
| [`tools/`](tools/README.md)   | Build and dev tooling: [`firmware_project`](tools/firmware/README.md), flashing, formatting. |

There are no per-year directories: `main` is always the current car, and past
seasons live in tags and maintenance branches (see
[CONTRIBUTING.md](CONTRIBUTING.md#season-policy)).

## Prerequisites

- [Bazelisk](https://github.com/bazelbuild/bazelisk) (installs the pinned
  Bazel from `.bazelversion` automatically)
- Git

That's it — compilers (ARM GCC for firmware, LLVM for host code) are hermetic
and downloaded by Bazel. No Xcode, MSVC, or system GCC needed for C++.
STM32CubeMX is only needed when changing a board's peripheral configuration.

On Windows, builds run locally instead of on remote executors; for full remote
execution use WSL2 (see the comments in [.bazelrc](.bazelrc)).

## First steps

Build the VCU firmware:

```bash
bazel build //boards/VCU:vcu
```

Run all tests (on the BuildBuddy Linux executors by default):

```bash
bazel test //...
```

Run the VCU simulator on your machine:

```bash
bazel run --config=local //boards/VCU:vcu_sim
```

Flash a board over ST-Link:

```bash
bazel run //boards/VCU:openocd
```

## Remote build setup (BuildBuddy)

Builds stream to and cache on [lhre.buildbuddy.io](https://lhre.buildbuddy.io).
To authenticate, copy [.bazelrc.user.example](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc.user.example) to
`.bazelrc.user` (gitignored) and paste your API key from BuildBuddy →
Settings → API keys — ask a software lead for an invite to the org.

No key, or no internet? `--config=local` builds entirely on your machine.
