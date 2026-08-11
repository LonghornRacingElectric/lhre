# ADR-002: Bazel with hermetic toolchains, not CMake

- **Status:** Accepted
- **Date:** 2026-08 (backfilled — the decision predates this record)

## Context

Members join every year on macOS, Linux, and Windows laptops, and the core
workflow ([ADR-003](003-lhal.md)) compiles the same application code for
four STM32 families *and* the host in one graph. CMake is the
embedded-industry default and what most vendor code assumes.

## Decision

Bazel, with everything hermetic: ARM GCC, host LLVM, Python, OpenOCD,
dfu-util, and clang-format are pinned in `MODULE.bazel` and downloaded on
first build, so onboarding is `git clone && bazel test //...`. Tests run
on BuildBuddy's remote Linux executors with a shared cache by default.

The full record — the detailed case, the cost accounting, and the whys
behind every fork, pin, and patch — is
[build-system.md](../build-system.md). This ADR is the summary; that page
is the source of truth and must be updated with any build-system change.

## Alternatives considered

- **CMake.** The default, and better-supported by vendor tooling — but it
  replaces hermetic toolchains with a per-OS tools-installation README
  that drifts, and cross-compiling four MCU families while host-testing in
  the same build is its weakest spot. See
  [build-system.md](../build-system.md) for the point-by-point case.
- **STM32CubeIDE projects.** Per-machine IDE state, no host-test story, no
  usable CI story, and no way to build the whole car at once.

## Consequences

- Zero-setup onboarding and identical toolchains on every machine and CI.
- Host tests, firmware images, and Python tooling live in one command
  surface.
- The costs are real and deliberate: every vendor dependency needs a Bazel
  wrapper, IDE integration needs a compile-commands extractor, and Bazel
  expertise is rarer than CMake exposure — one-time maintainer work traded
  against per-member, per-machine setup costs paid forever.
