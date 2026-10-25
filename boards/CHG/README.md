## Charger projects

- [CHG](README.md): STM32G474CET6 charger controller infrastructure. App and
  host tests are ready; CubeMX/hardware bring-up is pending.
- [charger-display](charger-display/README.md): validated ESP32 touchscreen UI,
  currently built with Arduino CLI.
- [CHG library](lib/README.md): one UART contract and power
  budget implementation for both sides.

These initial projects do not add unverified firmware to `:all_firmware`; see
[CHG bring-up notes](BRINGUP.md).

# CHG

Charger control board, **STM32G474CET6**. The initial application is host-buildable;
CubeMX configuration and hardware bring-up are intentionally pending. CHG owns
charging decisions; the [ESP32 display](charger-display/README.md) is a UI.

## Targets

| Target | Purpose |
| --- | --- |
| `:chg_app` | LHAL-only application adapter around the shared UART peer. |
| `:chg_app_test` | Real ESP/CHG protocol traffic through LHAL host UART fakes. |

```sh
bazel test --config=local //boards/CHG:chg_app_test //boards/CHG/lib:all
```

The [project library](lib/README.md) owns framing, telemetry,
commands, timeouts and the numerical supply budget. App code depends on LHAL
interfaces, never ST HAL, matching the [VCU split](../VCU/README.md).

## Layout and next hardware step

- [App](App/README.md): `chg::App::Step()` and controller hooks, plus host tests.
- [Board](Board/README.md): the pending hardware-wiring checklist.
- [lib](lib/README.md): UART protocol peers, charge budget and shared tests.
- [charger-display](charger-display/README.md): ESP32 UI, build tools and hardware notes.
- [Bring-up notes](BRINGUP.md): the controller/display build boundary and pending work.
- `CHG.ioc`, `Core/`, linker/startup files and `Board/main.cpp`: not fabricated.
  These must be generated/configured for the actual charger PCB.

Once its peripheral allocation is ready, generate `boards/CHG/CHG.ioc` from
CubeMX for STM32G474CET6 using the repo's
[new-board workflow](../../CONTRIBUTING.md#adding-a-new-board). Then run:

```sh
bazel run //tools:new_board -- boards/CHG/CHG.ioc --force --no-sim
```

`--force` replaces this **temporary host-only BUILD file** with the scaffolder's
`firmware_project` call; existing App files are retained. Add
`app_deps = ["//boards/CHG/lib:controller_link"]` to that generated call (plus
`//boards/CHG/lib:charge_budget` when used). Adapt the generated Board starter to
CHG's actual `Peripherals`, and replace its LED-only loop with the communications
and controller scheduling. If FreeRTOS is selected, provide the task entry that
calls `Step()`; this initial App intentionally does not select an RTOS.

Only after a real firmware image builds should `//boards/CHG:release` be added
to `//boards:all_firmware`. There is no CHG flash or release target yet.

## Controller boundary

The initial adapter supplies no pack measurements, readiness, charger outputs,
precharge sequencing, BMS/CAN integration or charge permission. START remains
unsupported until both action hooks are supplied, and still passes the shared
peer's readiness/fault checks. The real start callback must atomically recheck
live safety inputs. STOP, communication timeout and a new UI session must reach
an independent latched stop path. No automatic restart is implemented.

A UART write failure latches a transport fault, invokes the stop hook once and
blocks subsequent START. Clearing the latch requires reinitializing the App
under the real controller's recovery policy; no UI command clears it.