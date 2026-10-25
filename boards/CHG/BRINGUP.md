# Charger bring-up

## Context

The charger UI has been brought up on an ESP32-32E/ST7796S touchscreen with
Arduino core3.3.8 and TFT_eSPI2.5.43 as an offscreen drawing engine. The repo's
firmware macro targets STM32, not Xtensa/ESP32. The charger STM32G474CET6 pin
configuration is not ready yet, but its portable UART peer already runs against
the display. The display is part of the CHG project under `boards/CHG/charger-display/`.

## Current layout and build

- `boards/CHG` follows the existing App/Board/Core ownership split. Until its real
  CubeMX project is available it exposes only a host-buildable LHAL application
  and test. The repo scaffolder will generate its eventual firmware macro call.
- `boards/CHG/charger-display` temporarily builds through a local Arduino CLI script.
  Core and library versions are explicit; the display-library fetch is checksum
  verified. Firmware images/toolchains/caches are not checked in. The Bazel
  package exposes a source filegroup, not a pretend ESP32 firmware target.
- Platform-independent protocol and budget code live once in `boards/CHG/lib`.
  Their host tests and the CHG application test are part of `bazel test //...`.
  The ESP32 build consumes that same Arduino-compatible source directory.
- No STM32 flash/release/debug targets or `all_firmware` entry are created until
  the actual CubeMX output and hardware wiring exist.

## Alternatives considered

- Copy VCU's generated project: would silently select its pins, clocks and
  package configuration rather than the charger PCB's.
- Copy the UART protocol into each board: permits wire-format drift.
- Add an Xtensa/Arduino Bazel toolchain now: substantial build-system work beyond
  basic charger bring-up and unnecessary to preserve the validated hardware path.

## Consequences

The shared application and protocol remain CI-testable while hardware setup is
pending. ESP32 firmware compilation is an explicit additional check and uses an
installed Arduino core, so it is not yet a hermetic Bazel release. These notes describe CHG bring-up only; a future ESP32 toolchain can replace
the local display build.

See the [CHG project](README.md), [display guide](charger-display/README.md) and
[project library](lib/README.md) for build commands and integration.
