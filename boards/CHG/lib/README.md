# Charger transport and power budget

One platform-independent source for the [CHG controller](../README.md)
and [ESP32 charger display](../charger-display/README.md). No LHAL,
Arduino or ST headers are needed by the shared library.

| Bazel target | Contents |
| --- | --- |
| `:display_link` | C++11 display-side session, telemetry model and requests. |
| `:controller_link` | C99 controller peer with explicit application callbacks. |
| `:charge_budget` | Header-only C99/C++11 numerical AC/BMS/thermal/profile cap. |
| `:protocol_test` | Both peers, corruption/replay, watchdogs, ACKs and STOP priority. |
| `:budget_test` | Invalid inputs and constraint invariants across supply profiles. |

```sh
bazel test --config=local //boards/CHG/lib:all //boards/CHG:chg_app_test
```

For a quick compiler-only check before fetching Bazel's toolchains:

```sh
python3 boards/CHG/lib/test/run_native.py
```

The Arduino library metadata exposes the same `src/` to the display's build
script. This library belongs to CHG; the controller and its display consume the same
wire definitions. Its Bazel visibility is restricted to CHG packages.

Read the [wire contract](PROTOCOL.md) and [pack/supply policy](PACK_AND_SUPPLY.md).
Those calculations are reference material, not operational authorization. Real
profiles, BMS limits and charger outputs belong to CHG's eventual controller.
