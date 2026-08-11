# ADR-003: App code depends on LHAL interfaces, never ST HAL

- **Status:** Accepted
- **Date:** 2026-08 (backfilled — the decision predates this record)

## Context

Application logic written directly against ST's HAL can only run on the
target: no unit tests on laptops or CI, no simulators, and code coupled to
one STM32 family's headers. Most members most of the time don't have a
board on their desk.

## Decision

A thin layer of pure-virtual peripheral interfaces —
[LHAL](../../drivers/lhal/README.md) — with two backends: `lhal::stm32::*`
adapters over ST HAL handles (firmware) and `lhal::host::*` in-memory
fakes (tests and sims). Application code in `boards/*/App/` depends on
`//drivers/lhal` only.

The rule is enforced by the build, not by review: ST HAL packages are
non-public, so a direct dep on `//drivers/stm32/...` from app code is a
visibility *error*, and the fix is to add an LHAL interface — never a
visibility grant.

Two deliberate boundaries:

- **The RTOS is not abstracted.** App code uses the raw FreeRTOS API,
  which stays host-testable because the same kernel version builds for the
  host too (see [drivers/freertos](../../drivers/freertos/README.md)).
- **Board bring-up stays at the ST HAL level.** Clocks, pin mux, and
  peripheral handles are board code (`Board/main.cpp`); LHAL adapters wrap
  handles the board configured. Peripherals without an abstraction yet use
  ST HAL directly via each adapter's `handle()` escape hatch.

## Alternatives considered

- **ST HAL directly in app code.** No host tests or sims; every module is
  tied to one family's headers.
- **Mocking ST HAL itself.** The surface is enormous, macro-heavy, and
  different per family — mocks would drift from real behavior and need
  reworking for every new MCU family.
- **A vendor-neutral framework (Zephyr, mbed).** Replaces the whole
  CubeMX + ST HAL flow the EEs and existing tooling are built around;
  mbed is discontinued. A thin in-house layer keeps ST's stack underneath
  and abstracts only what host-testing actually needs.
- **Abstracting FreeRTOS too.** Rejected — an RTOS wrapper is a large API
  for zero test benefit once the kernel itself builds on host.

## Consequences

- Every board gets host unit tests and a simulator essentially for free
  ([the scaffolder](../../CONTRIBUTING.md#adding-a-new-board) generates
  working ones).
- New peripheral use in app code costs an interface + adapter + fake first
  (see [Adding a peripheral abstraction](../../drivers/lhal/README.md#adding-a-peripheral-abstraction))
  — deliberate friction that keeps the abstraction honest.
- The visibility fence means the architecture survives contributors who
  haven't read this document.
