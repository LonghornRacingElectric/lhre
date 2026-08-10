# Drivers

Hardware support, split by who owns the abstraction:

- [lhal/](lhal/README.md) — **Longhorn HAL**: our platform-independent
  peripheral interfaces. Application code depends on this and nothing else,
  so it builds for both the MCU and the host (tests, sims).
- [stm32/](stm32/README.md) — vendor code: ST's HAL, CMSIS, and FreeRTOS,
  packaged from pinned upstream commits, one subpackage per STM32 family.
