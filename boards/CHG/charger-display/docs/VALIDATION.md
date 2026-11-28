# Validation record — 2026-10-24

- Demo, monitor and explicit engineering control modes compile with ESP32 core3.3.8.
- The final demo app was flashed to the identified ESP32 over the replacement cable. Espressif write verification passed.
- The user confirmed the dashboard is visible and fills the screen after the standard-SPI transport change.
- Portable ESP C++11 and STM32 C99 peer interoperability tests pass with strict compiler warnings.
- The same UART test suite passed AddressSanitizer and UndefinedBehaviorSanitizer checks.
- Tests exercise CRC/COBS framing, generated corruption, replay/session guards, readiness, STOP preemption, ACK correlation, timeout and rollover.
- Charge-budget tests pass for supply/BMS/thermal/profile/voltage limits, invalid values, numerical overflow and constraint sweeps.
- Browser-preview script validation passes all30 scenario/tab combinations.

The final runtime log reports `touch=1` after the calibration workflow, confirming saved calibration. The user confirmed touch calibration and bottom-tab navigation both work. Final runtime samples show stable254668-byte free heap, increasing uptime and274ms full-frame drawing time. Actual STM32 hardware, UART wiring, isolation, BMS sensing, charger outputs and an HV pack were not part of these tests. The portable STM32 peer and numerical budget helper need integration with the team's actual controller firmware and safety architecture. No valid original flash backup was obtained.
