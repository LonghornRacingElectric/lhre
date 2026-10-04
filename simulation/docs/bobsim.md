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

## Four-post sweep

`make four-post-sweep CARS=<dir>` runs the Modelica four-post on each
vehicle YAML in `<dir>`. A study writes these files under `out/<study>/`.

```bash
make four-post-sweep CARS=out/anti-geometry/cars STUDY_WORKERS=3
```

For each car, the target:

1. Copies the pinned BobSim and BobLib from `git archive` into
   `out/.staging/four-post-sweep/<car>/`.
2. Writes the car as the copy's `vehicle.yml` and runs BobSim's record
   generator in the copy.
3. Runs `make standard-build-four-post` and the four-post eval in the copy.
4. Deletes the copy.

The sweep never writes into `bobsim/`, so you do not need
`make records-undo`. The outputs go to `<dir>/../four-post/`:

| File | Content |
| ---- | ------- |
| `<car>.csv` | The four-post metrics of one car |
| `summary.csv` | One row per car with every metric, the wall time, or the error |
| `logs/<car>.log` | The generator, build and eval output |

Each car compiles its own model. Run-time hardpoint overrides do not work:
the compiled model accepts them for the right side only. A car fails if the
four-post log says "It is not possible to override".

Limits at `4da577a`:

- One car takes about 3 min: about 2 min to compile and a few seconds to
  evaluate. Three cars in parallel took 3 min and 3.4 GB of memory.
- The eval takes spring and bar rates from BobSim's four-post config, not
  from the car file.
- The cars of the anti-geometry study do not compile. OpenModelica stops
  with "System is structurally singular" for `ad60_as00` and `ad00_as60`.
  The baseline vehicle compiles.

## The pin

The pin is the submodule commit. To move it, follow
[bump-bobsim](skills/bump-bobsim/SKILL.md). One PR moves the pin and fixes
anything the move breaks.

## Windows

- `make init` checks out the submodules with LF endings and long paths on.
  A CRLF checkout makes BobSim report every generated record as stale.
- Do not clone lhre with `--recurse-submodules` on Windows. If you did,
  run `git submodule deinit -f simulation/bobsim`, then `make init`.
