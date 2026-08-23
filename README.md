# LHRe

[![postsubmit](https://github.com/LonghornRacingElectric/lhre/actions/workflows/postsubmit.yml/badge.svg)](https://github.com/LonghornRacingElectric/lhre/actions/workflows/postsubmit.yml)

Longhorn Racing Electric's monorepo: firmware for every ECU on the car, the
hardware abstraction layer it's built on, and the tooling to build, test, and
flash it all with Bazel.

<div class="grid cards" markdown>

-   :material-swap-horizontal:{ .lg .middle } **CAN Matrix & Signals**

    ---

    Full CAN ID allocation, message timings, signal bit layouts, and telemetry bindings generated from spec.

    [:octicons-arrow-right-24: Open CAN Matrix](docs/can-matrix.md){ .md-button .md-button--primary }

-   :material-chip:{ .lg .middle } **Boards & Firmware**

    ---

    Per-ECU firmware layout, the VCU reference board, pinouts, and the new-board scaffolder.

    [:octicons-arrow-right-24: Explore Boards](boards/README.md){ .md-button }

-   :material-layers-outline:{ .lg .middle } **LHAL & Drivers**

    ---

    Platform-independent hardware abstraction layer enabling 100% host-testable embedded code.

    [:octicons-arrow-right-24: Read LHAL Guide](drivers/lhal/README.md){ .md-button }

-   :material-bug-outline:{ .lg .middle } **Hardware Debugging**

    ---

    Zero-setup VS Code debugging with ST-Link, hermetic gdb, OpenOCD, and SVD peripheral viewer.

    [:octicons-arrow-right-24: Debugging Guide](tools/debug/README.md){ .md-button }

</div>


## Layout

| Directory  | What lives there |
| ---------- | ---------------- |
| [`drivers/`](drivers/README.md) | Hardware. [LHAL](drivers/lhal/README.md) (our platform-independent HAL) and [vendor HAL packages](drivers/stm32/README.md). |
| [`lib/`](lib/README.md)     | Platform-agnostic C++ that builds on host **and** target (ring buffers, CRC, CAN pack/unpack, …), with colocated tests. |
| [`apps/`](apps/README.md)    | Host-side software: telemetry, dashboards, gateways. |
| [`boards/`](boards/README.md)  | Per-ECU firmware projects ([`boards/VCU`](boards/VCU/README.md) is the reference layout). |
| [`tools/`](tools/README.md)   | Build and dev tooling: [`firmware_project`](tools/firmware/README.md), flashing, [VS Code debugging](tools/debug/README.md), formatting. |

There are no per-year directories: `main` is always the current car, and past
seasons live in tags and maintenance branches (see
[CONTRIBUTING.md](CONTRIBUTING.md#season-policy)).

The decisions that shape the repo — monorepo, Bazel, LHAL, docs conventions
— are recorded as ADRs in [docs/architecture](docs/architecture/README.md).
The build system gets its own long-form record — why Bazel, and the whys
behind its forks, pins, and patches — in
[docs/build-system.md](docs/build-system.md).

## Prerequisites

- [Bazelisk](https://github.com/bazelbuild/bazelisk) (installs the pinned
  Bazel from `.bazelversion` automatically)
- Git

That's it — compilers (ARM GCC for firmware, LLVM for host code) are hermetic
and downloaded by Bazel. No Xcode, MSVC, or system GCC needed for C++.
STM32CubeMX is only needed when changing a board's peripheral configuration.

On Windows, builds run locally instead of on remote executors; for full remote
execution use WSL2 (see the comments in [.bazelrc](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc)).
Windows also needs the `startup --output_user_root=C:/_bzl` line uncommented
in `.bazelrc.user` (copied from
[.bazelrc.user.example](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc.user.example)) —
without it, Python codegen tools blow Windows' 260-char path limit and die
with an `ImportError` in the protobuf runtime.

## The commands

Everything goes through Bazel, and the surface is small enough to memorize.
Shown for the VCU; swap `VCU`/`vcu` for any board — the targets are uniform
because [`firmware_project`](tools/firmware/README.md) generates them.

| To…                        | Run |
| -------------------------- | --- |
| Build one board's firmware | `bazel build //boards/VCU:vcu` |
| Build + test everything    | `bazel test //...` |
| Run a board's simulator    | `bazel run --config=local //boards/VCU:vcu_sim` |
| Flash over ST-Link         | `bazel run //boards/VCU:openocd` |
| Flash over USB (DFU)       | `bazel run //boards/VCU:dfu` |
| Format all C/C++           | `bazel run //tools/format` |
| Fix IDE red squiggles      | `bazel run //:refresh_ide` |

`bazel test //...` runs on the BuildBuddy Linux executors by default (see
below); add `--config=local` to any command to stay entirely on your
machine. Green `bazel test //...` on a fresh clone means you're set up —
there is nothing else to install.

When the convention doesn't cover you — a file the build won't pick up, a
new dependency, vendoring a library, a test target that isn't found — see
the [cookbook](docs/cookbook.md).

Debug on hardware: open the repo in VS Code, install the recommended
Cortex-Debug extension, plug in the ST-Link, and hit F5 — breakpoints,
FreeRTOS thread views, and peripheral registers, with nothing else to
install on any OS (see
[CONTRIBUTING.md](CONTRIBUTING.md#debugging-on-hardware-vs-code)).

## Remote build setup (BuildBuddy)

Builds stream to and cache on [lhre.buildbuddy.io](https://lhre.buildbuddy.io).
To authenticate, copy [.bazelrc.user.example](https://github.com/LonghornRacingElectric/lhre/blob/main/.bazelrc.user.example) to
`.bazelrc.user` (gitignored) and paste your API key from BuildBuddy →
Settings → API keys — ask a software lead for an invite to the org.

No key, or no internet? `--config=local` builds entirely on your machine.
