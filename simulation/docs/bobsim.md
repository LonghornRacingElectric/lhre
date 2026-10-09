# BobSim

[BobSim](https://github.com/BobDyn/BobSim) is the runtime for every study.
It is a git submodule at `simulation/bobsim`, pinned to one commit. BobSim
brings [BobLib](https://github.com/BobDyn/BobLib), the Modelica vehicle
library, as its own submodule.

BobSim is GPL-3.0. lhre is MIT. The submodule keeps the two apart. Do not
copy BobSim code into lhre.

## Tiers

| Tier | What it is | Typical targets | Needs OpenModelica |
| ---- | ---------- | --------------- | ------------------ |
| Geometry | `kin_py` kinematics | `shark-overlay` | No |
| Reduced order | `dyn_py`, 3 to 14 DOF | `reduced-eval`, `lap-eval`, `envelope-ggv`, `envelope-ymd` | No |
| High fidelity | BobLib Modelica, compiled | `standard-build`, `standard-eval-*` | Yes |
| High fidelity, stepped | BobLib `VehicleSim` as an FMI 2.0 FMU | `make fmu`, `make realtime-bench` | Yes |

Run any target with `make bobsim T=<target>`. `make bobsim T=help` lists
all of them.

## How long targets take

Measured on a Windows laptop with Docker Desktop, BobSim `7ff5191`:

| Command | Time |
| ------- | ---- |
| `make init` on a fresh clone (image cached) | 1 min 35 s |
| `make test` | under 10 s |
| `make records` | under 10 s |
| `make bobsim T=standard-build` | 2 min 20 s to 2 min 35 s |
| `make bobsim T=envelope-ggv` | 1 h 55 min, one core |

Measured on an Apple Silicon Mac with Docker Desktop (11.7 GB VM), BobSim
`f94e1e8`:

| Command | Time |
| ------- | ---- |
| `make fmu` | 25 min |
| `make realtime-bench` | 51 s |

Start long targets in the background. Do not run them in CI.

## Our vehicle and BobSim

- `make test` and studies load `vehicle/vehicle.yml` through
  `BOBSIM_VEHICLE`.
- BobSim targets do not read `BOBSIM_VEHICLE` yet. `make bobsim T=...` uses
  `simulation/bobsim/vehicle.yml` until BobSim reads the variable.
- The Modelica tier compiles BobLib's `.mo` records. Run `make records`
  first. It writes our vehicle into those records inside the submodule.
  Do not commit those files. `make records-undo` puts back only those files.
- At `4da577a`, BobLib's checked-in records do not match BobSim's generator
  output, even for BobSim's own `vehicle.yml`. Most of the difference is
  formatting. So BobSim's `sync-vehicle` reports "stale" before you change
  anything. This is not a problem with our vehicle.

## Stepping the car from outside

The autonomy stack needs a plant that it can drive one control period at a
time. BobSim has two candidates. `make realtime-bench` steps each one at
50 Hz with Orion's `vehicle.yml` and reports simulated time per wall time.

Measured on an Apple Silicon Mac, BobSim `f94e1e8`, 10 s runs:

| Model | Step steer 5 deg at 15 m/s | Launch from rest |
| ----- | -------------------------- | ---------------- |
| `dyn_py` 3 DOF | 2.9x real time | 6.1x |
| `dyn_py` 6 DOF | 3.8x | 5.5x |
| `dyn_py` 10 DOF (Radau) | 0.76x | 0.99x |
| `dyn_py` 14 DOF (Radau) | 1.3x | 1.2x |
| VehicleSim FMU | Fails at t = 0.38 s | Not run |

- All four `dyn_py` models start from 0 m/s and agree on the step steer
  (yaw rate 0.72 rad/s).
- The FMU steps at 1.5x to 3.2x real time until CVODE fails at a model
  event. With a 2 ms step it reaches t = 2.0 s. The cause is not known yet.
  fmpy prints FMU log messages without their arguments on arm64, so the
  next step is to debug on an x86 Linux machine.
- `VehicleSim` has no inputs. Its `StandardVCU` makes the steering and pedal
  commands. A closed-loop plant needs a BobLib variant with external
  inputs and without the termination monitors.
- The FMU build uses gcc. clang does not compile the generated start-value
  function (about 340,000 lines) in 400 s.

Nothing in `autonomy/` uses these models yet.

## The pin

The pin is the submodule commit. To move it, follow
[bump-bobsim](skills/bump-bobsim/SKILL.md). One PR moves the pin and fixes
anything the move breaks.

## Windows

- `make init` checks out the submodules with LF endings and long paths on.
  A CRLF checkout makes BobSim report every generated record as stale.
- Do not clone lhre with `--recurse-submodules` on Windows. If you did,
  run `git submodule deinit -f simulation/bobsim`, then `make init`.
