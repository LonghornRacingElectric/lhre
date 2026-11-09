# VCU software-in-the-loop (SIL)

The unmodified VCU app (`vcu::App`, the same `:vcu_app` library the firmware
links) driving a car model. Every tick the car model's state reaches the app
as the sensor voltages and CAN frames the car would give it, and the app's
`INVERTER_TORQUE_COMMAND`, decoded off the bus, drives the car model. Nothing
in `App/` knows it is in a simulation. The decision and the alternatives are
in [ADR-020](../../../docs/architecture/020-vcu-sil-fmu-host.md).

```
 scenario (driver, HVC, shutdown loop)
        │
        ▼
 vcusil host (Python) ──reads──▶ car model: OrionTorqueFMI.fmu (BobLib, fmpy)
        │                              ▲
        │ physical values              │ torque command, brake, steering
        ▼                              │
 libvcu_sil.so ── Rig: ADC volts, GPIO, CAN frames at spec rates
                  └─▶ vcu::App::Step() ──▶ INVERTER_TORQUE_COMMAND (decoded)
```

## The car model

The SIL needs a torque-input car: `OrionTorqueFMI`, BobLib's `VehicleFMI`
with its VCU replaced by one that takes `motorTorqueCommand` and
`regenTorqueLimitCommand`, so the firmware, not the model, turns pedal travel
into torque. `make fmu` does not build it yet: it builds the pedal-input
`OrionFMI`, which the host refuses ("FMU interface mismatch"). Today the
torque FMU is built by hand with BobLib's exporter, and its build needs two
OpenModelica runtime fixes that [ADR-016](../../../docs/architecture/016-fmu-artifact-boundary.md)
wants upstream first. Until then, get a copy built for your CPU from whoever
maintains the SIL, with the `provenance.json` its build writes beside it.

BobLib is GPL, so the FMU is never committed (ADR-016). It must also be built
for the CPU it runs on: OpenModelica files every 64-bit Linux build under
`binaries/linux64`, so the host reads the ELF header and refuses a mismatch.
It refuses an FMU without a matching `provenance.json` beside it unless given
`--allow-unverified-fmu`.

## Running it

Docker is the only prerequisite:

```bash
boards/VCU/Sil/run.sh --fmu path/to/OrionTorqueFMI.fmu
```

That builds `//boards/VCU:libvcu_sil.so` from your checkout in the SIL dev
container ([Dockerfile](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/Dockerfile)),
runs the `launch` scenario and writes `boards/VCU/Sil/out/<scenario>-<time>/`:

| File          | What                                                              |
| ------------- | ----------------------------------------------------------------- |
| `summary.txt` | States, time to DRIVE, speeds, faults, car-model warnings.        |
| `plot.png`    | Speed, torque (command vs model), DC power, pedals, VCU state.    |
| `trace.csv`   | One row per 3 ms step: model outputs, driver inputs, firmware internals, what the model was given. Floats round-trip exactly. |
| `console.log` | The firmware's own debug-UART log (state changes, build stamp).   |
| `fmu.log`     | The OpenModelica runtime's output; the cause of a model failure.  |
| `meta.json`   | Status and provenance: FMU and library hashes, firmware build stamp, git state, whether the FMU's `vehicle.yml`, tire files and BobSim pin match the checkout. |

Exit status: 0 the run completed, 1 the car model failed, 2 anything else
(a firmware that does not build, a wrong FMU, bad options, a harness bug).
After 1 or 2, `meta.json` says what failed and the trace so far is kept.

Other options pass through to `python -m vcusil`:

| Option | Default | What |
| ------ | ------- | ---- |
| `--scenario` | `launch` | Power up, ready to drive on the brake, full pedal from rest for 4 s, coast. |
| `--soc` | 0.9 | Initial state of charge, 0..1. At 1.0 the model's pack sits at `V_dc_max` and accepts no regen. |
| `--event-mode` | 0 | lhre `EventMode`: 0 boots like the car (ENDURANCE); 1-4 pick that event's parameter set. |
| `--brake-psi-full` | 1500 | Line pressure that is full brake in the car model (see below). |
| `--allow-unverified-fmu` | off | Run an FMU without a matching `provenance.json`. |
| `--no-plot` | off | Skip `plot.png`. |
| `--out DIR` | `out/<scenario>-<time>` | Where the run goes (a run.sh option). |

To see a firmware change, edit `App/` and run again: the library rebuilds
from the checkout. For example, set `PowerLimitParams::limit_w` in
`App/config/params.hpp` to 40 kW: the power cap engages, its trim settles
the model's DC power at 40.0 kW, and top speed falls from about 32.5 to
26.9 m/s.

```bash
boards/VCU/Sil/run.sh --test --fmu path/to/OrionTorqueFMI.fmu
```

runs the C++ tests below and the host's pytest suite. Without `--fmu` the
tests that need the car model skip.

## Targets

| Target              | What                                                          |
| ------------------- | ------------------------------------------------------------- |
| `:vcu_sil_rig`      | `vcu::sil::Rig`: the app on `test/bench.hpp`'s fakes, fed physical values. Host only. |
| `:vcu_sil_rig_test` | The rig against the real app: startup into DRIVE, sensor round trips, frame rates, torque decoding, bounded buffers, determinism, a toy car. CI runs it on Linux, on Windows and under UBSan. |
| `:vcu_sil_api`      | The C header, [vcu_sil.h](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/vcu_sil.h). Linux only. |
| `:libvcu_sil.so`    | The rig behind that C ABI. Exports only `vcu_sil_*`; libc++ is linked in, so it needs only glibc. Linux only. |
| `:vcu_sil_test`     | The ABI as a host sees it, linked against the `.so`: validation, layout table, symbol hiding. Linux only. |

They are declared in `boards/VCU/BUILD.bazel`, not in a `Sil/` package,
because `:vcu_app` is private to `//boards/VCU`. Nothing under `Sil/` is in
the firmware: the `firmware_project` macro only globs `App/`, `Board/`,
`Core/` and `USB_Device/`.

## How a step works

The host steps in 3 ms, the control period (`App::kControlPeriodMs`). At
t = 3k ms it:

1. reads the car model's outputs at t. Several outputs feed through directly
   from its inputs, so it reads before it sets anything;
2. calls `vcu_sil_advance(inputs, 3)`: the rig holds the inputs for three
   1 ms passes. Each pass completes both ADC scans, sends the frames due at
   that ms, calls `App::Step()`, then drains every host buffer. The control
   tick runs on the first pass, so it sees the car at t;
3. applies that tick's torque command to the car model over [t, t + 3) and
   steps it.

Time is virtual and nothing is threaded, so a run is deterministic: the same
inputs give the same trace, bit for bit.

## Signals

The rig converts with the firmware's own calibration (`App/config/params.hpp`),
so each sensor reads back what the car model reports.

| Firmware input | Source | Path |
| -------------- | ------ | ---- |
| APPS1/2 | driver pedal travel | both sensors, `AppsParams` endpoints |
| BSE1 / BSE2 | driver brake psi, front / rear | `BseParams` transfer function |
| BSE_3 (PDU, 100 Hz) | rear psi | the model has no line lock |
| Hard-brake pin | both BSE pins over `hard_brake_pin_v` | comparator trimmed to the firmware threshold, computed from the same ADC counts |
| Line lock readback | `Linelock_Ctrl AND NOT Hard_braking` | the board's gate, not valve feedback |
| Other BSPD pins, brake flags, motor disconnect | low | |
| Steering | steering angle, deg | inverse of `ComputeSteering()`; clamped short of the sensor's wrap |
| RTD switch, shutdown loop | scenario | the switch is a toggle: releasing it leaves DRIVE |
| CONTACTOR_STATUS (100 Hz) | scenario HVC state | the model has no HVC |
| BATTERY_CELL_LIMITS (10 Hz) | `dcBusVoltage` / `Ns` | per-cell voltage under load |
| INVERTER_SPEED (333 Hz) | `motorSpeed` rad/s → signed rpm, `motorTorqueActual`, `dcBusVoltage` | |
| INVERTER_VOLTAGE (100 Hz), INVERTER_CURRENT (10 Hz) | `dcBusVoltage`, `dcBusCurrent` | positive = discharge on both sides |
| INVERTER_FAULTS / DETAILS (100 Hz), INDICATORS (10 Hz), DUI (10 Hz) | zeros | |
| Wheel speeds (Data, 333 Hz) | `wheelSpeedFL..RR` | magnitude: a toothed-wheel sensor has no direction |

Each node sends at its spec rate (`longhorn::PeriodMs` of the generated
`kFrequencyHz`). Car-model values and the steering angle are clamped to what
their sensor or CAN field can carry, and flagged in `outputs.saturated`. The
other driver and rest-of-car values must be in range, or the call fails:
those are scenario bugs.

The car model gets the torque request only while the frame's `enable` is set,
the frame's `torque_limit` as its regen limit, the driver's brake directly
(it never passes through the VCU), and the steering angle in radians. A
reverse `direction` is refused: the model cannot take it.

## Assumptions and known limits

- **Brake scale.** The model has no hydraulics: its brake input is 0..1 of
  1500 N·m. `--brake-psi-full` maps line pressure onto it. It is a
  placeholder, not a measured constant.
- **The car model may not be this commit's car.** `meta.json` and the summary
  say when the FMU's `vehicle.yml`, tire files or BobSim pin differ from the
  checkout.
- **Model behaviour to know before blaming the firmware** (BobLib):
  - the rear driveline rings after a torque step from rest. In the launch
    its period shortens from about 100 to 55 ms (10 to 18 Hz) as speed
    builds, and it dies out over about 0.7 s. The motor and rear wheels turn
    backward in 18 of the 3 ms steps between 2.067 and 2.166 s. The firmware
    sees `|w|` and a signed rpm; the summary reports the backward spin;
  - at or near full model brake the car reverses (measured on this FMU:
    full brake from 15 m/s ends at -2.6 m/s after 1 s), so scenarios stay
    well under `--brake-psi-full`;
  - with no brake and no torque the car creeps backward (about 1 m in 30 s);
  - BobLib's own VCU stage still caps the command at 230 N·m and 80 kW of
    torque times speed before the motor, and the motor and inverter add
    their own limits; `torqueLimitFlags` in the trace shows which acted;
  - after 120 s of simulated time the motor peak timer derates it to 109.8 N·m;
  - the model's pack is 140s4p; `RegenParams` assumes 132s3p.
- **Not modelled:**
  - the BSPD, apart from its hard-brake comparator: no power sense, timer,
    latch or relay, so `BSPD_TRIPPED` never happens. The summary warns when
    hard braking with more than 5 kW lasted 500 ms, which would trip the car;
  - the line lock valve, so regen cannot be tested: BSE3 always equals BSE2
    and the model keeps braking the rear wheels hydraulically. Regen is off
    in every event set;
  - the RTOS: `App::Step()` stands in for the FreeRTOS tasks, so task
    priorities and scheduling jitter are absent;
  - coolant (the TSM is silent, which raises nothing), `INVERTER_STATUS` (the
    speed fallback), CAN latency and loss, and the inverter's own response
    (torque applies within the step).

## Python host (`host/`)

`vcusil` is a plain package, not a Bazel target; it runs in the dev container.

- `firmware.py`: ctypes binding. Loading checks the ABI version and every
  struct field's offset against `vcu_sil_layout()`, so a header change that
  is not mirrored fails at load.
- `plant.py`: the FMU. Checks the interface and the binary's CPU, sets
  `SOC_start`, reads `Ns` (an Integer), captures the runtime's output.
- `scenario.py`, `cosim.py` (the loop), `report.py`, `provenance.py`.
- `tests/`: pytest. `VCU_SIL_LIB` and `VCU_SIL_FMU` select the library and
  the FMU; `run.sh --test` sets both. Only the tests that need the FMU skip
  without it. CI does not run this suite yet.

## Gotchas

- Change `vcu_sil.h` and `host/vcusil/firmware.py` together, and bump
  `VCU_SIL_ABI_VERSION`. The layout check catches a missed field, not a
  changed meaning.
- One thread at a time, across all handles in a process: the host FreeRTOS
  port keeps process-wide state.
- `run.sh` mounts the repo read-only and keeps Bazel's cache in the
  `lhre-sil-bazel-<uid>` Docker volume (several GB). It refuses to start with
  less than 10 GB free on the repo's disk.
- The Bazel targets build on any host, but the `.so` and its test are Linux
  only. On a Mac, use `run.sh`.
