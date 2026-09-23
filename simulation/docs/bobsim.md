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
  Do not commit those files. `make records-undo` puts them back.
- At `7ff5191`, BobLib's checked-in records do not match BobSim's generator
  output, even for BobSim's own `vehicle.yml`. Most of the difference is
  formatting. So BobSim's `sync-vehicle` reports "stale" before you change
  anything. This is not a problem with our vehicle.

## The pin

The pin is the submodule commit. To move it, follow
[bump-bobsim](skills/bump-bobsim/SKILL.md). One PR moves the pin and fixes
anything the move breaks.

## Windows

- `make init` checks out the submodules with LF endings and long paths on.
  A CRLF checkout makes BobSim report every generated record as stale.
- Do not clone lhre with `--recurse-submodules` on Windows. If you did,
  run `git submodule deinit -f simulation/bobsim`, then `make init`.
