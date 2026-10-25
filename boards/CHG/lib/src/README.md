# Shared sources

Portable C99 controller transport, C++11 display transport and the numerical
charge-budget helper. This is also the Arduino library's `src/` directory, so
both board builds use identical framing and telemetry definitions. Keep MCU,
LHAL and Arduino dependencies out of these sources. See the
[library README](../README.md) for targets and protocol documentation.
