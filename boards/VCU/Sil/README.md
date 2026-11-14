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

That checks the scenario file for errors, builds
`//boards/VCU:libvcu_sil.so` and the CAN decode table from your checkout in
the SIL dev container ([Dockerfile](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/Dockerfile)),
runs the scenario (`launch` unless `--scenario` names another; see
[Scenarios](#scenarios)) and writes `boards/VCU/Sil/out/<scenario>-<time>/`:

| File          | What                                                              |
| ------------- | ----------------------------------------------------------------- |
| `run.mcap`    | The whole run for Foxglove: every firmware signal, every CAN frame decoded, the car model's truth, event markers. See [Run log](#run-log). |
| `summary.txt` | States, time to DRIVE, speeds, faults, car-model warnings, and the scenario file with its description and warnings. |
| `plot.png`    | Speed, torque (command vs model), DC power, pedals, VCU state.    |
| `trace.csv`   | One row per 3 ms step: model outputs, driver inputs, firmware internals, what the model was given, then the model truth as `truth_*`. Floats round-trip exactly. |
| `console.log` | The firmware's own debug-UART log (state changes, build stamp).   |
| `fmu.log`     | The OpenModelica runtime's output; the cause of a model failure.  |
| `meta.json`   | Status and provenance: the scenario file's path, hash, content and warnings, FMU and library hashes, firmware build stamp, git state, whether the FMU's `vehicle.yml`, tire files and BobSim pin match the checkout. |
| `scenario.yml` | The scenario file, byte for byte.                                |

Exit status: 0 the run completed, 1 the car model failed, 2 anything else
(a scenario file that cannot run, a firmware that does not build, a wrong
FMU, bad options, a harness bug). Once the run has started, `meta.json` says
what failed and the trace so far is kept. A run that stops before it starts
writes nothing: a scenario file that cannot run (checked before anything
builds), bad options or a firmware that does not build leave no run
directory, and a reused `--out` as it was. `run.sh` prints `wrote DIR` only
for a run that wrote it.

Other options pass through to `python -m vcusil`:

| Option | Default | What |
| ------ | ------- | ---- |
| `--scenario` | `launch` | A name from `scenarios/` or a `.yml` file inside the checkout; see [Scenarios](#scenarios). |
| `--soc` | the scenario's `setup`, else 0.9 | Initial state of charge, 0..1. At 1.0 the model's pack sits at `V_dc_max` and accepts no regen. |
| `--event-mode` | the scenario's `setup`, else 0 | lhre `EventMode`: 0 boots like the car (ENDURANCE); 1-4 pick that event's parameter set. |
| `--brake-psi-full` | 1500 | Line pressure that is full brake in the car model (see below). |
| `--allow-unverified-fmu` | off | Run an FMU without a matching `provenance.json`. |
| `--no-plot` | off | Skip `plot.png`. |
| `--out DIR` | `out/<scenario>-<time>` | Where the run goes (a run.sh option); `<scenario>` is the name, or a file's stem. |
| `--can-table` | set by run.sh | The decode table; without it `run.mcap` keeps frames undecoded on `/can/raw`, and `/vcu/status` has no `loop_state_name` or `event_mode_name` (they read `?`). |

`run.sh`'s own options (`--fmu`, `--scenario`, `--out`, `--view`, `--port`)
take their value after a space or an `=`.

`run.sh --check [--strict] [--explain] [--errors-only] [--brake-psi-full PSI] [NAME|FILE ...]`
checks scenario files with `python3 -m vcusil.check` in the container (with
no names, every file in `scenarios/`; `--scenario X` adds X). It needs
Docker, and builds or reuses the SIL image, but it never builds the
firmware. [Scenarios](#scenarios) shows the same check without Docker.

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

## Scenarios

A scenario is a scripted test drive: what the driver and the rest of the car
(HVC, AIRs, shutdown loop) do over time. Each is a YAML file in `scenarios/`,
named for its file: `scenarios/part-throttle.yml` runs with
`--scenario part-throttle`. You write no Python.

| Scenario | What |
| -------- | ---- |
| [`launch`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/scenarios/launch.yml) | Power up, ready to drive on the brake, full pedal from rest for 4 s, coast. The reference to copy. |
| [`part-throttle`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/scenarios/part-throttle.yml) | 40 % then 60 % pedal with ramps: the pedal map. |
| [`shutdown-loop-open`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/scenarios/shutdown-loop-open.yml) | The loop opens at 60 % pedal: the VCU must cut torque and go to FAULT. |
| [`brake-over-throttle`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/scenarios/brake-over-throttle.yml) | Braking at 60 % pedal: torque stays cut until the pedal is under 5 %. |

**Write one in five minutes.**

Every command here runs from the repo root.

1. `cp boards/VCU/Sil/scenarios/launch.yml boards/VCU/Sil/scenarios/my-test.yml`.
   Names are lowercase words with dashes.
2. Change `description` and the blocks under `at:`.
3. Check it: `PYTHONPATH=boards/VCU/Sil/host python3 -m vcusil.check my-test`
   needs Python 3.11 or newer with PyYAML, and no Docker and no build.
   Without them (a system Python may refuse `pip install PyYAML`),
   `PYTHONPATH=boards/VCU/Sil/host uv run --no-project --python 3.12 --with PyYAML python -m vcusil.check my-test`
   fetches both for the one command.
   `boards/VCU/Sil/run.sh --check my-test` is the same check in the SIL
   container. Each problem names the file and, where it has them, the line
   and the fix. `--explain` shows
   when each change reaches the firmware.
4. Run it with `boards/VCU/Sil/run.sh --fmu PATH --scenario my-test`, then
   `boards/VCU/Sil/run.sh --view boards/VCU/Sil/out/my-test-<time>`. Each
   `mark` shows in the Log panel.

**The file** (schema `lhre.vcu-sil-scenario.v1`), here a short launch:

```yaml
# Comments say why the drive does what it does.
schema: lhre.vcu-sil-scenario.v1
description: Power up, ready to drive on the brake, half pedal for 2 s, coast.
duration_ms: 5000

setup:
  soc_start: 0.9  # at 1.0 the car model's pack accepts no regen

at:
  # HV off, shutdown loop closed, foot on the brake.
  - t_ms: 0
    brake_psi: 350.0

  - t_ms: 200
    hvc_state: PRECHARGING

  - t_ms: 600
    hvc_state: ENERGIZED
    contactors_closed: true

  - t_ms: 1000
    mark: rtd
    rtd_pressed: true

  - t_ms: 1500
    brake_psi: 0.0

  - t_ms: 2000
    mark: pedal-on
    accelerator: {to: 0.5, ramp_ms: 200}

  - t_ms: 4000
    mark: pedal-off
    accelerator: 0.0
```

| Key | Type | Required | Rule |
| --- | ---- | -------- | ---- |
| `schema` | string | yes | Exactly `lhre.vcu-sil-scenario.v1`. Another `lhre.vcu-sil-scenario.vN` gets "written for a different harness version". |
| `description` | string | yes | One line, 1..120 characters. Shown by `check`, in `summary.txt` and in `meta.json`. |
| `duration_ms` | integer | yes | 1..600000. The run has `ceil(duration_ms / 3)` steps, and the last sample is at `3 * (ceil(duration_ms / 3) - 1)`. 7000 gives 2334 steps, the last at 6999. |
| `setup` | mapping | no | Run conditions (below). |
| `allow` | mapping | no | `warning_code: reason`, one line per warning (below). |
| `at` | list of blocks | yes, at least 1 | The timeline (below). |

`setup`: the command line wins, then the file, then the default.
`meta.json` records where each value came from in `setup_from` (`cli`,
`scenario` or `default`). `--brake-psi-full` stays a command-line option: it
calibrates the car model; it is not part of the drive.

| Key | Type | Range | Default | CLI override |
| --- | ---- | ----- | ------- | ------------ |
| `event_mode` | string | lhre `EventMode` name: `UNASSIGNED` (boots like the car: ENDURANCE parameters), `ACCELERATION`, `SKIDPAD`, `AUTOCROSS`, `ENDURANCE` | `UNASSIGNED` (0) | `--event-mode 0..4` |
| `soc_start` | number | 0..1 | 0.9 | `--soc` |

`allow`: each key is a warning code from the table under Checks (except
`unused_allow`), and its value a one-line reason. An allowed warning is still
recorded in `meta.json` (`scenario_allowed`, with the reason) and named in
`summary.txt`, but it does not print and does not fail `--strict`. An
allowed code that never fires is the `unused_allow` warning.

Blocks, each a list item under `at:`:

| Key | Type | Rule |
| --- | ---- | ---- |
| `t_ms` | integer | Required. 0 ≤ `t_ms` < `duration_ms`. Strictly greater than the previous block's `t_ms`. |
| `mark` | string | Optional. A name of at most 40 characters, lowercase words with dashes, unique in the file. |
| signals | below | A block must set at least one signal or a `mark`. |

| Key | Layer | Type | Unit | Error outside | Default | Ramp |
| --- | ----- | ---- | ---- | ------------- | ------- | ---- |
| `accelerator` | driver | number | fraction of pedal travel, both APPS | 0..1 (ABI) | 0.0 | yes |
| `brake_psi` | driver | number | line psi, front and rear alike | 0..3000 (ABI, `BseParams::full_scale_psi`) | 0.0 | yes |
| `steering_deg` | driver | number | steering-wheel deg | -180..180 (the sensor saturates at about -188.9 / +180.6) | 0.0 | yes |
| `rtd_pressed` | driver | bool | RTD toggle switch | `true` / `false` | false | no |
| `hvc_state` | rest_of_car | string | lhre `HvcState` name | `NOT_ENERGIZED`, `PRECHARGING`, `ENERGIZED`, `CHARGING_PRECHARGING`, `CHARGING` | `NOT_ENERGIZED` | no |
| `contactors_closed` | rest_of_car | bool | both AIRs | `true` / `false` | false | no |
| `shutdown_loop_v` | rest_of_car | number | V at both sense taps | 0..27 (ABI) | 24.0 | yes |

- One block per moment something changes, in time order: `- t_ms: N`, with
  the block's other keys lined up under `t_ms`. A value holds until a later
  block changes it; a signal no block sets keeps its
  default (`Driver()` and `RestOfCar()` in `scenario.py`). The keys are
  those dataclasses' fields, so a file, `/driver`, `/rest_of_car` and
  `trace.csv` share one vocabulary. `contactors_closed` is never derived
  from `hvc_state`, so a fault drive can contradict it.
- `{to: X, ramp_ms: N}` ramps from the held value to X over N ms, in a
  straight line. Only numbers ramp. The ramp must end by the next change to
  that signal: at its time or before. The same two keys may instead go on their own lines,
  indented under the signal.
- Top-level keys start at the left margin, in any order. One indented as far
  as a block's keys is part of that block, and an unknown key there; one at
  the column of the dashes is a syntax error.
- Text needs no quotes, unless it has `: ` or ` #` in it, starts with a
  symbol such as `[`, `{`, `*` or `&`, or would read as a number, `true`,
  `false` or `null`. A ` #` starts a comment even in the middle of unquoted
  text, so `description: pedal #2` is an error, not a description of `pedal`.
  A comment after text has a space after its `#`: `  # why`. A description
  or reason written as a `>` or `|` block uses `>-` or `|-`, since `>` and
  `|` keep a line break at the end.
- Use `#` comments for explanations.
- Save the file as UTF-8 without a byte-order mark, which some Windows
  editors add. The loader is strict; Why YAML, below, lists its rules.

**When the firmware sees it.** The host samples every 3 ms, so a change at
`t_ms` takes effect at 3·⌈t_ms/3⌉, between 0 and 2 ms late, and a ramp is a
staircase of 3 ms holds. The HVC state and contactors then wait for the next
10 ms CONTACTOR_STATUS frame, and the firmware's filters and debounces
follow:

| Signal | Path and delay |
| ------ | -------------- |
| `accelerator` | ADC, then an EMA with α 0.35 (τ ≈ 7 ms), then the 9 % / 74 % deadzone. Brake-over-throttle uses the raw value. |
| `brake_psi` | ADC, then an EMA with α 0.10 (τ ≈ 28.5 ms), seeded by the first sample. Also sent as BSE_3 at 100 Hz. |
| `rtd_pressed` | GPIO, with a 20 ms debounce. Written at 1000, sampled at 1002, DRIVE at 1023. The switch reads pressed from boot until `rtd_pressed` has been false through the 20 ms debounce (the 21 ms sample, for a file that starts false), so a press first sampled at 21 ms or earlier never counts. A release is debounced the same way: DRIVE ends 21 ms after the sample that sees it. |
| `shutdown_loop_v` | ADC, with a 10 ms debounce. Reads UNKNOWN (SHUTDOWN_OPEN) for the first 10 ms of every run. |
| `hvc_state`, `contactors_closed` | The CONTACTOR_STATUS frame, sent at multiples of 10 ms. Written at 200: step 201, frame 210, PRECHARGE at 210. Written at 600: TS_ACTIVE at 600, RTD_WAIT at 603. |
| `steering_deg` | Telemetry, and the car model's handwheel angle. |

In launch, RTD pressed at 1000 ms enters DRIVE at 1023 ms. A `mark` is
reported at the first sample at or after its block's `t_ms`, and
`check --explain`'s step column is that sample; a ramp still shows the held
value there and moves from the next sample. Explain also gives, for
`hvc_state` and `contactors_closed`, the tick that acts on the next
CONTACTOR_STATUS frame;
and for an `rtd_pressed` change, or a `shutdown_loop_v` change that crosses
a loop threshold, the tick the firmware's debounce takes it
(`rtd_pressed true (the firmware sees it at 1023 ms)`) or why the firmware
never sees it. Those ticks come from the lint's models of the firmware
(Checks, below).

**Checks.**

- A file that breaks the format or a range the C ABI enforces does not run
  (exit 2), before anything builds: every problem is listed at once, as
  `file:line: error: where: message [code]`. Only the YAML reader's own
  problems come first and alone: the first syntax error PyYAML meets, or
  every refusal of the strict loader (Why YAML, below), since the checks
  need the file read. The codes are `not_found`,
  `bad_name`, `yaml_syntax`, `wrong_schema`, `missing_key`, `unknown_key`,
  `wrong_type`, `out_of_range`, `bad_value`, `time_order`, `sets_nothing`,
  `bad_ramp`, and, once the rest is clean, the timing errors
  `ramp_overlap`, `ramp_past_end` and `unsampled`. `unsampled` is a value no
  3 ms sample sees: a step is seen from the first sample at or after its
  `t_ms`, a ramp only from the first sample after its `t_ms` (it starts at
  the held value), and either until the next change to that signal, or
  through the next change's own sample when that change is a ramp.
- Firmware traps are warnings, because fault drives cross them on purpose.
  A value "held" is a step, or a ramp's end, that lasts at least one sample,
  counting the start sample of a ramp after it, which still shows it; a
  ramp passing through a band does not warn. Where the firmware's past
  decides, the rules run a model of it, one control tick per 3 ms sample:
  the RTD switch's debounce (`EdgeButton`), the brake and pedal filters,
  brake-pressed and the shutdown loop's taps. The thresholds are
  `check.THRESHOLDS`, copied from `params.hpp` and tested against it, and
  `tests/test_check_firmware.py` holds each model to the library. The
  check runs models rather than the library so that it needs no firmware
  build: `run.sh --check` never builds one, and the check runs natively on
  a Mac, where `libvcu_sil.so` does not build.

| Code | Fires when | Why (firmware) |
| ---- | ---------- | -------------- |
| `brake_near_threshold` | A held `brake_psi` within 25 psi of 30, 250, 300 or 531 (bands 5–55, 225–325, 506–556) | 30 psi is the RTD brake (`brake_light_psi`); 300/250 the brake-pressed hysteresis on the filtered average; about 531 psi the hard-brake comparators (`hard_brake_pin_v` 0.80 V on both sensors). Too close to the threshold to rely on; inside 250..300 the previous state decides. |
| `brake_high` | A held `brake_psi` > ⅔ × `--brake-psi-full` (1000 psi at the default) | The car model reverses near full brake |
| `pedal_in_deadzone` | A held `accelerator` in (0, 0.09] ("asks for no torque") or [0.74, 1.0) ("asks for full-pedal torque") | `min_travel_deadzone` / `max_travel_deadzone` |
| `loop_near_threshold` | A held `shutdown_loop_v` in 16.10..19.67 V | Within 0.5 V of the loop's low (16.60 V) or high (19.17 V) threshold (pins 2.00/2.31 V), or between them, where it reads what it read before |
| `brake_over_throttle` | At a sample s: `accelerator > 0.23` while the modelled filtered brake (EMA α 0.10 per sample, seeded by sample 0) may hold brake-pressed: from where it reaches 275 psi until it falls under 225. Reported once per stretch, at its first sample. | BOT latches on brake-pressed (on at 300, off under 250 psi of filtered brake) with a raw pedal over 25 %, and holds torque at 0 until the pedal is under 5 %. 275/225 covers every tick the firmware's 300/250 is pressed. |
| `rtd_at_boot` | An RTD press before the switch has read released for 20 ms since boot: `rtd_pressed` true at 0, or, in a file that starts false, a press first sampled at 21 ms or earlier | `EdgeButton` starts as pressed and reports a press only after it has read released, so the firmware never sees this press |
| `rtd_too_short` | An `rtd_pressed` change that does not hold through the sample its 20 ms debounce passes on (s + 21): it changes back, or the run ends, first | The firmware never sees the press or the release |
| `rtd_without_brake` | At the debounced press edge e (s + 21): the modelled filtered brake < 55 psi | RTD needs filtered brake ≥ 30 psi (`brake_light_psi`) at the edge. The message says the firmware refuses it only under 29 psi. |
| `rtd_with_pedal` | At the debounced edge: the modelled filtered accelerator (EMA α 0.35) > 0.05 | `rtd_max_pedal` = 0 after the 9 % deadzone (`min_travel_deadzone`). The message says the firmware refuses it only over 0.091. |
| `rtd_before_ready` | For a press first sampled at s, with its edge at e: some sample in [s-50, e] is not (ENERGIZED, contactors closed, loop over 19.67 V, above the `loop_near_threshold` band) | A margin, not the firmware's own test: RTD_WAIT trails these by the 100 Hz CONTACTOR_STATUS frames and the loop's 10 ms debounce, so the edge may come outside RTD_WAIT, which uses it up |
| `hvc_contactors_disagree` | `(hvc_state == ENERGIZED) != contactors_closed` for more than 100 ms of the file's time, from the block that starts it to the block that ends it (or `duration_ms`) | `Energized` needs both; this catches a forgotten `contactors_closed: true` |
| `unused_allow` | An `allow` entry that never fired | The drive no longer tests what it claims |

- If a warning is the point of the drive, add a `code: reason` line under
  `allow:`, as `brake-over-throttle.yml` does.
- `--strict` fails on any warning that is not allowed (for CI).
- `check --brake-psi-full PSI` takes the run's value (default 1500), which
  `brake_high` depends on.
- `--errors-only` reports errors only, with one `ok` or `FAIL` line per
  file, and exits 2 on any error. It cannot go with `--strict` or
  `--explain`. `run.sh` checks with it before every run, so the warnings
  print once, from the run, with the run's own `--brake-psi-full`.

**Provenance.** `meta.json` records the file's path (from the repo root, or
absolute outside it), sha256 and parsed content (`scenario_file`,
`scenario_parameters`), and its warnings. `run.mcap` attaches the file
unchanged as `scenario.yml`, and the run directory keeps a copy.
`trace.csv` depends only on the parsed content: a comment-only edit changes
the hash and `run.mcap`, not the trace.

**Why YAML.** The rest of lhre writes its inputs in YAML (`vehicle/vehicle.yml`,
`simulation/studies/*/study.yml`), so a scenario reads like them. Plain
YAML is loose, though: `yaml.safe_load` keeps the last of two equal keys,
reads `no` and `off` as false, `010` as 8 and `1_000` as 1000, and expands
anchors into blocks the file never spells out. So `scenario_file.py` reads a
strict YAML with PyYAML (6.0.3, pinned in `host/requirements.txt`): `true`,
`false`, `null` and decimal numbers read as YAML 1.2 reads them and
everything else is text, a leading zero (`010`: 8 to YAML 1.1, 10 to YAML
1.2) does not load, and the usual slips are errors at their line.
`yaml.safe_load` follows YAML 1.1, so it still reads some values otherwise
(`mark: on` as true, `1e3` as text), and the tests hold the shipped files to
reading the same to it.
The rules:

- `true` and `false` (also `True`, `TRUE`, `False`, `FALSE`) are booleans;
  `null`, `~` or nothing is null; `0`, `7000` or `-5` is an integer; a
  number with a `.` or an exponent, or `.inf` or `.nan`, is a float.
  Everything else is text, `yes`, `off`, `1_000`, `0x1F`, `2026-01-01` and
  `12:30` included, so where a number or `true`/`false` belongs it is a
  `wrong_type` error with the fix (`yes`: write true; `1_000`: write 1000).
  A quoted value is always text.
- A number with a leading zero (`010`, `-007`, `00.5`) is refused: YAML 1.1
  reads `010` as 8, YAML 1.2 as 10.
- A key is a bare name. A quoted, numeric, `true`/`false`/`null` or `? `
  key is an error, and so is a key written twice in one mapping, naming
  both lines.
- No anchors (`&`), aliases (`*`), merge keys (`<<`) or tags (`!`,
  `!!str`): each block is written out.
- One document: a leading `---` is fine; a second document, and a `%YAML`
  or `%TAG` directive, are errors. An empty or comment-only file is the
  empty document, so its missing keys are reported. The top level must be
  keys, not `null`.
- UTF-8 without a byte-order mark, and no U+2028, U+2029, U+0085 or carriage
  return without a line feed, which end a line for some readers and not
  others. A tab outside quotes (PyYAML reads none, even before a comment;
  `\t` in double quotes is fine), a no-break space where a space goes, an
  unclosed `{`, `[` or quote, a missing space after `:` or a block's `-`, a
  key without its `:`, a block without its dash, a `: ` inside unquoted
  text, and a ` #` that would cut it short (`pedal #2`) or leave it empty
  (`description: #2`), are errors with the fix.
- Text has no control or invisible character other than a tab, such as
  `"\e"` or U+200B, which `check` would print, and no `"\ud83d\ude80"`
  surrogate escapes, which YAML does not join into one character: write the
  emoji itself. A raw control character is an error anywhere, comments
  included.
- Nesting deeper than 100 levels, an integer longer than Python reads and a
  `"\U00110000"` escape past the last character are errors, not crashes.
  A message writes a value or tag that does not print in quotes, escaped.

**Not expressible yet:** APPS1 different from APPS2, front brake different
from rear, sensor volts, a single AIR, separate loop taps, pins, and CAN
delays or loss. The C ABI or the host drives these together today. A
firmware parameter change, such as a 40 kW power limit, is still an edit to
`params.hpp`. v1 rejects every unknown key, so a harness given a file
written for a later one fails loudly instead of misreading it.

## Targets

| Target              | What                                                          |
| ------------------- | ------------------------------------------------------------- |
| `:vcu_sil_rig`      | `vcu::sil::Rig`: the app on `test/bench.hpp`'s fakes, fed physical values. Host only. |
| `:vcu_sil_rig_test` | The rig against the real app: startup into DRIVE, sensor round trips, frame rates, torque decoding, bounded buffers, determinism, a toy car. CI runs it on Linux, on Windows and under UBSan. |
| `:vcu_sil_api`      | The C header, [vcu_sil.h](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/vcu_sil.h). Linux only. |
| `:libvcu_sil.so`    | The rig behind that C ABI. Exports only `vcu_sil_*`; libc++ is linked in, so it needs only glibc. Linux only. |
| `:vcu_sil_test`     | The ABI as a host sees it, linked against the `.so`: validation, layout table, snapshot, frame capture, symbol hiding. Linux only. |
| `:vcu_sil_can_table` | `vcu_sil_can.json`: every message in `lib/spec` with its bit layout, scale, unit and enum names, for the host's decoder. Generated by `:vcu_sil_gen_can_table` (`gen_can_table.py`). A message with a big-endian or multiplexed signal, which the host cannot decode yet, is listed under `unsupported` and its frames stay raw: such a signal never fails the build (an invalid spec still does). Linux only. |
| `:vcu_sil_gen_can_table_test` | The generator on the real spec, and on copies with a big-endian signal, a multiplexed message and an overlap (which must still fail). Linux only. |

They are declared in `boards/VCU/BUILD.bazel`, not in a `Sil/` package,
because `:vcu_app` is private to `//boards/VCU`. Nothing under `Sil/` is in
the firmware: the `firmware_project` macro only globs `App/`, `Board/`,
`Core/` and `USB_Device/`.

## Run log

`run.mcap` holds the run as JSON messages, one topic per subsystem, stamped
in simulated time from 0. Copies of the run's `meta.json` and `summary.txt`
are attached, with the scenario file as `scenario.yml`, and its metadata
names the scenario (with `scenario_path` and `scenario_sha256`), status,
firmware build (and whether the tree was dirty) and FMU hash. No wall-clock time goes in:
the attached copies leave out `wall_s`, the wall time in the summary, and
the message counts (`mcap`, known only once the log is written). So the same
run writes a byte-identical file. The one exception is an output directory
inside the checkout that git does not ignore: `meta.json` records the
checkout's `git status`, so an earlier run's files there show up as changes.
`run.sh`'s default, `out/`, is ignored.

| Topic | What |
| ----- | ---- |
| `/driver`, `/rest_of_car` | The scenario's inputs. |
| `/vcu/status` | The firmware's state with its name and last transition reason, faults (as a mask and by name), shutdown loop, event mode, motor rpm, torque command. |
| `/vcu/pedals`, `/vcu/steering`, `/vcu/analog`, `/vcu/digital_in`, `/vcu/digital_out`, `/vcu/torque`, `/vcu/battery`, `/vcu/wheel_control` | The rest of the firmware's control snapshot (`ControlSnapshot`, all 111 fields), stamped at its control tick. |
| `/vcu/inverter_command` | The torque command as the inverter decodes it, and the rig's saturation flags. |
| `/can/vcu/<MESSAGE>` | Every frame the VCU sent, decoded by the spec. |
| `/can/car/<MESSAGE>` | Every frame the rig sent the VCU as the rest of the car, decoded. |
| `/can/raw` | Frames the decode table has no entry for, as bytes: IDs not in the spec, and messages it lists as `unsupported`. Without `--can-table`, every frame. |
| `/model/outputs`, `/model/inputs` | The car model's outputs (plus `dcPower`, V x I) and what it was given. |
| `/model/truth` | What the car's sensors cannot see: chassis velocity and acceleration, tire loads, tire slip states, steer angles, halfshaft and brake torque, SOC, pack and inverter limits, the model's own torque limits. |
| `/events` | `foxglove.Log` markers: state changes with their reason, faults raised and cleared, saturation, the model turning backward, the BSPD condition, the scenario's marks (`scenario`, at the first sample at or after each block's `t_ms`), the end of the run. |

To look at a run:

```bash
boards/VCU/Sil/run.sh --view boards/VCU/Sil/out/<run>
```

serves it with [`foxglove/vcu_sil.json`](https://github.com/LonghornRacingElectric/lhre/blob/main/boards/VCU/Sil/foxglove/vcu_sil.json)
in Lichtblick (an open-source Foxglove fork, pinned by digest) on
`127.0.0.1:18080` (`--port` picks another). In the Foxglove app, open `run.mcap` as a local file and
import that layout (Layouts, Import from file). The layout puts model truth
beside what the firmware sees: true speed against the firmware's wheel-speed
estimate, true tire slip against its slip estimate, the torque path stage by
stage. An end-to-end test checks every topic and field the layout names
against a real run, so a rename shows up as a failing test, not an empty
panel.

Model truth comes from the FMU's internal variables, looked up by name. A
rebuilt FMU may rename some; `meta.json` lists the ones not found
(`plant.truth_missing`) and the run goes on without them; `plant.truth_units`
gives each one's unit. A value that is not finite is logged as null, a gap in
the plot. Brake torque is one corner per axle (`brakeTorqueFL`,
`brakeTorqueRL`): BobLib gives each wheel half its circuit's torque.
`motorModelTorqueCommand` is the torque the model's motor was asked for,
after its own limits. Tire slip `kappa_*` (unitless) and slip angle `alpha_*`
(rad) are derived from the tire deformation states, using BobLib's 0.5 m
relaxation length, only when the FMU's `PTX1`/`PTY1` are 0
(`plant.slip_from_states`). Tire Fx and Fy are not exported: BobLib keeps
them in a protected part of the chassis, so they need a BobLib change.

## How a step works

The host steps in 3 ms, the control period (`App::kControlPeriodMs`). At
t = 3k ms it:

1. reads the car model's outputs at t. Several outputs feed through directly
   from its inputs, so it reads before it sets anything;
2. calls `vcu_sil_advance(inputs, 3)`: the rig holds the inputs for three
   1 ms passes. Each pass completes both ADC scans, sends the frames due at
   that ms, calls `App::Step()`, then drains every host buffer. The control
   tick runs on the first pass, so it sees the car at t;
3. reads the firmware's control snapshot and every captured frame, then
   applies that tick's torque command to the car model over [t, t + 3) and
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
- `scenario.py` (the timeline a scenario file compiles to), `cosim.py` (the
  loop), `report.py`, `provenance.py`.
- `scenario_file.py`: loads scenario files with a strict YAML loader (see
  Why YAML under [Scenarios](#scenarios)) and checks them.
- `check.py`: the warnings, the models of the firmware they stand on,
  `--explain`, and `python -m vcusil.check`. It needs only PyYAML beyond
  the standard library, so it runs on any Python 3.11 or newer that has it.
- `candecode.py`: decodes captured frames with the generated table.
- `events.py`, `mcaplog.py`: the event markers and `run.mcap`.
- `tests/`: pytest. `VCU_SIL_LIB`, `VCU_SIL_CAN_TABLE` and `VCU_SIL_FMU`
  select the library, the decode table and the FMU; `run.sh --test` sets
  them. Only the tests that need the FMU skip without it.
  `test_check_firmware.py` holds each of `check.py`'s models to the
  library. `test_run_sh.py` runs `run.sh` with stub `docker`, `df`,
  `mktemp` and `date` first on `PATH`, so it checks what each container is
  handed and what a run leaves in its directory without Docker or the
  clock. The script that runs inside the container is exercised only by
  real runs. CI does not run this suite yet.

## Gotchas

- Change `vcu_sil.h` and `host/vcusil/firmware.py` together, and bump
  `VCU_SIL_ABI_VERSION`. The layout check catches a missed field, not a
  changed meaning. The saturation bits are copied by hand three times:
  `vcu_sil.cpp` checks the rig's `Saturation` against `VCU_SIL_SAT_*` at
  compile time, and `test_firmware.py` checks the binding's
  `SATURATION_BITS` against the bit each clamp sets.
- One thread at a time, across all handles in a process: the host FreeRTOS
  port keeps process-wide state.
- The wide `vcu_sil_snapshot` struct is not mirrored by hand: the binding
  builds it from the layout table's field types. The layout table, the
  snapshot struct and `FromSnapshot()` in `vcu_sil.cpp` must list the same
  fields; `:vcu_sil_test` checks that the table leaves no gap wider than
  alignment padding, and cross-checks the snapshot against
  `vcu_sil_outputs` where the two overlap. Nothing checks that
  `FromSnapshot()` fills a field added to `ControlSnapshot`: add it to all
  three.
- Lichtblick and Foxglove keep a layout in browser storage once loaded. After
  the layout file changes, import it again (or clear the site's storage).
  After seeking, Lichtblick's Log panel shows only the latest event: press
  play from the start to see them all. Its "high frequency topics" alert is
  about the 333 Hz topics and is harmless.
- Some CAN spec units look wrong (`%` for values the firmware sends as a
  0..1 fraction, such as `ACCELERATOR_PEDAL.accelerator_pedal_travel` and
  `BRAKES.brake_bias`). The decoder logs the spec's values as they are.
- `check.THRESHOLDS` copies the `params.hpp` fields its warnings stand on.
  Change one of those fields and `test_check.py` fails until the copy
  follows; it also fails if `params.cpp` overrides one for an event mode.
- `check.py`'s models mirror longhorn's `EdgeButton` and `Ema`, `pedals.cpp`
  (the filters, brake-pressed, brake-over-throttle) and `ShutdownLoop`.
  Change those and `test_check_firmware.py` fails until the model follows.
- `run.sh` mounts the repo read-only and keeps Bazel's cache in the
  `lhre-sil-bazel-<uid>` Docker volume (several GB). It refuses to start with
  less than 10 GB free on the repo's disk.
- The rig and its test build on any host. The `.so`, its test, the decode
  table, its generator and the generator's test are Linux only. On a Mac,
  use `run.sh`.
