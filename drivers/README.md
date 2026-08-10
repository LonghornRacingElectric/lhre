# Drivers

Hardware support, split by who owns the abstraction:

- [lhal/](lhal/README.md) — **Longhorn HAL**: our platform-independent
  peripheral interfaces. Application code depends on this and nothing else,
  so it builds for both the MCU and the host (tests, sims).
- [longhorn/](longhorn/README.md) — **Longhorn library**: shared board
  services (status LED, and eventually CAN services, DFU, logging) written
  against LHAL only, so they build and test on both MCU and host.
- [stm32/](stm32/README.md) — vendor code: ST's HAL and CMSIS (one
  subpackage per STM32 family) plus the family-independent USB Device
  middleware, packaged from pinned upstream commits.
- [freertos/](freertos/README.md) — the FreeRTOS kernel, pinned once for
  firmware (Cortex-M ports) and host tests/sims (simulator ports), so task
  code runs unmodified on both.
