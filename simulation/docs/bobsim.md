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
| Geometry | KinPy kinematics (`engines/kinpy`) | `shark-overlay` | No |
| Reduced order | DynPy, 3 to 14 DOF (`engines/dynpy`) | `reduced-eval`, `lap-eval`, `envelope-ggv`, `envelope-ymd` | No |
| High fidelity | BobLib Modelica, compiled | `standard-build`, `standard-eval-*` | Yes |

Run any target with `make bobsim T=<target>`. `make bobsim T=help` lists
all of them.

## How long targets take

Measured on a Windows laptop with Docker Desktop, BobSim `7ff5191`:

| Command | Time |
| ------- | ---- |
| `make init` on a fresh clone (image cached) | 1 min 35 s |
| `make test` | about 40 s at BobSim `fe03bab` (see [tests.md](tests.md)) |
| `make records` | under 10 s |
| `make bobsim T=standard-build` | 2 min 20 s to 2 min 35 s |
| `make bobsim T=envelope-ggv` | 1 h 55 min, one core |

Start long targets in the background. CI runs only the standard
evaluations, through `make car`. See [tests.md](tests.md#ci).

## Our vehicle and BobSim

- `make test` and studies load `vehicle/vehicle.yml` through
  `BOBSIM_VEHICLE`.
- BobSim targets do not read `BOBSIM_VEHICLE` yet. `make bobsim T=...` uses
  `simulation/bobsim/vehicle.yml`. To run a BobSim target on our car, use
  `make car T=...`. See [Run a BobSim target on our car](#run-a-bobsim-target-on-our-car).
- The Modelica tier compiles BobLib's `.mo` records. `make records`
  writes our vehicle into those records inside the submodule, at
  `bobsim/engines/boblib`. Do not commit those files. `make records-undo`
  puts back only those files.
- Our vehicle has the same name as BobSim's default car. So `make records`
  replaces BobSim's record for that car until `make records-undo`.

### Run a BobSim target on our car

With `make records` alone, the Modelica model uses our records, but the
Python steps of the target read `bobsim/vehicle.yml`. FourPost, for
example, reads its target loads and spring data from that file. The run
then mixes two cars and gives no error. `make car` prevents this.

```bash
make car T=standard-eval-four-post
make four-post-acceptance T=standard-eval-four-post
```

`make car T=<target>` does these steps:

1. It stops if `bobsim/` has local changes.
2. It runs `make records`. The generator must report the records as
   written for our vehicle, inside `bobsim/engines/boblib`.
3. It copies `vehicle/vehicle.yml` to `bobsim/vehicle.yml`. The copy has
   the same bytes, so the same sha256. Entries under `paths:` resolve from
   the BobSim root, so they still resolve. The generator checks the
   installed file again. It must report the records as written.
4. It deletes the old files in `bobsim/simulations/mbd/generated_results/`.
   At BobSim `fe03bab`, FourPost reads its spring free length from the
   metrics CSV of the previous run in that folder
   (`four_post_eval_sim.py`, `_metrics_csv_value`). A CSV from another car
   would change this run.
5. It runs `make bobsim T=<target>`.
6. It copies the outputs to `out/car/<target>/`: the metrics, and the
   manifest, overrides, `run.log` and result CSV (under 1 MiB) of each new
   Modelica run. It never copies a `.tir` file.
7. It fails if a Modelica executable that ran is older than the records.
   Such an executable was built before our records, so it can be for
   another car.
8. It always puts back `bobsim/vehicle.yml` and runs `make records-undo`,
   also after a failure or Ctrl+C. Then it fails if
   `git -C bobsim status --short --ignore-submodules=none` prints anything.
9. It writes `out/car/<target>/identity.json`: the lhre commit and dirty
   state, the BobSim and BobLib commits, the vehicle and tire sha256, the
   records state, the image id, the target, its exit status and the
   sha256 of each output.

If a `make car` run stops before step 8, for example on a power cut, run
`git -C bobsim checkout -- vehicle.yml` and `make records-undo`.

`make four-post-acceptance T=<target>` reads `out/car/<target>/`. It
writes `four_post_acceptance.json` there. It fails when one of these is
true:

- The run failed, or it was for another target or another vehicle file.
- The metrics CSV is missing, or it changed after the run.
- A required metric is missing or has no finite value.
- The CSV has a `status` column and a row has a status other than `ok`.
- The pin reports a static balance or equilibrium failure.
- A FourPost run manifest sets a spring table or an anti-roll bar rate
  that differs from the vehicle file of the run.

A check that the pin cannot answer is `UNAVAILABLE`. It is not a pass.
At `fe03bab` three checks are unavailable. The CSV has no status column.
The CSV does not name its vehicle. The only static balance value,
`static_balance_pass`, compares target loads with themselves, so it is
not a measurement. The acceptance is a software check. It is not a
physical validation of the car.

At `fe03bab` our car fails the executed setup check. FourPost takes the
anti-roll bar rates from `model_overrides` in its own
`four_post_eval_config.yml`: 390 and 525 N·m/rad. Our `vehicle.yml` has
257.62 and 535.47 N·m/rad. `run.log` shows that the model applied 390 and
525. So the FourPost bar roll stiffness and load transfer are not for our
bars. BobSim PR #117 removes these overrides. Until the pin has a fix,
the FourPost check in CI fails for our car.

### Run a BobSim target on a candidate

A candidate is a changed copy of the car, for example with the frozen 2027
hardpoints. It lives in `out/<name>/` with its own `bundle.yml`. Do not
commit it. See
[edit-vehicle](skills/edit-vehicle/SKILL.md#check-a-candidate).

```bash
make car T=standard-eval-four-post VEHICLE=out/frozen-2027/vehicle.yml
make four-post-acceptance T=standard-eval-four-post VEHICLE=out/frozen-2027/vehicle.yml
```

`VEHICLE` is relative to `simulation/`. Without it, `make car` uses
`../vehicle/vehicle.yml`, which is `vehicle/vehicle.yml` at the repo root.
With it, `make car` changes these steps:

- It stops if `VEHICLE` is not a file directly in `out/<name>/`. A deeper
  folder is refused. The repo has one vehicle file. The container sees only the repo.
- After step 1, it runs the candidate check of `make check-vehicle` with
  `out/<name>/bundle.yml`. It stops on a problem, before it writes
  anything.
- It writes the records for the candidate and installs the candidate as
  `bobsim/vehicle.yml`.
- It writes the outputs to `out/car/<target>--<name>/`. `<name>` is the
  folder under `out/`. A run on
  `vehicle/vehicle.yml` writes to `out/car/<target>/`. Two cars never
  share a folder.
- `identity.json` names the candidate path and its sha256. It sets
  `vehicle.candidate` to `true`. The candidate is not the vehicle member
  of `vehicle/bundle.yml`. The `bundle` entry is `out/<name>/bundle.yml`.

`make four-post-acceptance` with the same `VEHICLE` reads
`out/car/<target>--<name>/`. It compares the executed setup with the
candidate. If you change the candidate after the run, the acceptance
fails.

### Modelica initialization

BobLib's `VehicleSim` starts from a solved quasi-steady-state (QSS) pose,
`pQSSInitialization`. That pose was solved for BobSim's own car. Since BobSim
`276e720` (#74), the generator keeps the pose only when the new record
matches the solved record exactly. Otherwise it stops and writes nothing,
unless the vehicle file sets `modelica_initialization: legacy`.

Our `vehicle/vehicle.yml` sets `legacy`. The reasons:

- Our car differs from the solved record, so the default (`preserve`)
  stops `make records`. BobSim's own `vehicle.yml` also differs from it.
  BobSim's `tests/test_modelica_generator.py` expects that.
- With `legacy`, `make records` writes a `VehicleSim` without the solved
  pose. The model starts from BobLib's settling start.
- At the old pin `4da577a`, `make records` did the same, without a
  setting and without a message. So `legacy` keeps the start that
  `make records` gave our car before the move.

A solved pose for our car needs a recalibrated BobLib record. That is
BobLib work. Until it exists, keep `legacy`.

## The pin

The pin is the submodule commit. To move it, follow
[bump-bobsim](skills/bump-bobsim/SKILL.md). One PR moves the pin and fixes
anything the move breaks.

After you pull a pin move, update your checkout from `simulation/`:

```bash
git -C bobsim submodule deinit -f --all
make init
```

The first command removes the nested submodules of the old pin. Without
it, a nested submodule that BobSim moved stays as an untracked folder.
`make init` gets the new submodules and builds the image again. The image
tag does not change with the pin, so an old image stays in use until you
build again.

### Current autonomy pin

PR #91 advances `fe03bab` to `22c5056` (BobSim main). BobLib remains at
`1bd6d78`. No vehicle numbers change. The autonomy adapter now imports
the public `engines.dynpy` package.

The following commits change physics or numerics since `fe03bab`:

| Commit | Class | Effect |
| ------ | ----- | ------ |
| `9d36539` (#121) | Physics/numerics | Close transient wheel torque and normal loads together; reject unresolved balance. |
| `9ebc220` (#122) | Physics | Balance planar corner loads for an off-center CG. |
| `61a5561` (#123) | Numerics | Preserve GGV feasibility during interpolation. |
| `22c9d66` (#124) | Physics/model input | Solve separate signed GGV branches. |
| `a765657` (#136) | Physics | Close planar roll/pitch moments at KinPy contact positions. |
| `304cb6e` (#142) | Model input | Require linked QSS inputs and retain envelope parameters. |
| `8c39a1a` (#143) | Model input | Preserve imported track widths and resample racing-line gates. |
| `e76475b` (#144) | Numerics | Use sparse analytic racing-line gradients. |

Other commits change packaging, replay, identity, reports and the analysis
workspace. `1acfe2d` (#139) changes the default lap reference to a fixed
minimum-curvature line. Old lap, envelope and autonomy study results are
historical until rerun at this pin. The ROS plant uses direct transient
stepping; it does not use BobSim's QSS lap planner.

### Old to new names

BobSim `6629ab8` (#82) and `6329270` (#97) moved its source. The pin moved
from `4da577a` to `fe03bab` in two steps. This table maps the names that
lhre used.

| At `4da577a` | At `fe03bab` |
| ------------ | ------------ |
| `_0_Utils.vehicle_io` | `common.vehicle_io` |
| `_0_Utils.kin_py.kinematics` | `engines.kinpy.kinematics` |
| `_0_Utils.dyn_py` | `engines.dynpy` |
| `_0_Utils.lap_sim.{qss,racing_line,track}` | `simulations.lap.core.{qss,racing_line,track}` |
| `_2_EnvelopeSim.{vehicle_loader,vehicle_yaml}` | `simulations.envelope.{vehicle_loader,vehicle_yaml}` |
| `_5_App.tire_eval` | `app.tire_eval` |
| `_5_App.modelica_generator` | `app.modelica_generator` |
| `_3_StandardSim/LapTimeEval/lap_time_eval_config.yml` | `simulations/lap/lap_time_eval_config.yml` |
| `_3_StandardSim/_modelica_runner.py` | `simulations/response/mbd/_modelica_runner.py` |
| `_3_StandardSim/generated_results/` | `simulations/mbd/generated_results/` |
| BobLib at `_0_Utils/external/BobLib` | BobLib at `engines/boblib` |

`make test` fails if lhre code or docs use a name from the left column.

### What changed in BobSim from `4da577a` to `fe03bab`

The move takes 43 BobSim commits. These 21 change physics, numerics or
model inputs. A result made at `4da577a` is stale when it depends on one
of them. Run the study again before you use it.

| Commit | PR | What changes |
| ------ | -- | ------------ |
| `739143a` | #70 | FourPost front anti-dive report sign. lhre adopted it first, in its own pin move. |
| `bfebb33` | #71 | OpenModelica 1.26.3 to 1.27.1. FourPost does not compile at this commit alone. |
| `4f25698` | #72 | BobLib `e03da04`: new joint reference frame. 7 ramp-steer metrics leave tolerance. |
| `276e720` | #74 | Generator keeps a solved QSS pose only on an exact record match. |
| `8d66f07` | #75 | DynPy suspension rates come from the requested vehicle. |
| `dc248ef` | #83 | KinPy quarter-car solver replaces the corner solver. |
| `816ac5f` | #84 | Rack and corner travel couple through KinPy in DynPy and GGV. |
| `58c7f58` | #86 | MF5.2 force module. |
| `1e95049` | #87 | MF5.2 replaces the tanh tire and the 0.8 friction floor. |
| `37a0057` | #88 | Front and rear tire fits stay separate. |
| `9054512` | #90 | MF5.2 moments and the full tire wrench. |
| `6cfac24` | #91 | Centered QSS derivatives near zero. |
| `9a56d44` | #92 | Out-of-fit tire trims fail. GGV retries roots. |
| `e8436f3` | #93 | QSS path-frame and rolling acceleration targets. |
| `29b3c88` | #95 | QSS height and attitude stay stationary on level roads. |
| `40cd2da` | #99 | DynPy slip-root search starts at zero slip. |
| `87ae3ca` | #101 | DynPy ramp and step steer path. |
| `b6dcd1e` | #103 | Implicit solver Jacobian probes and a separate ramp end time. |
| `d0e3d00` | #105 | PI speed control and Radau for 10 and 14 DOF. |
| `c0537ff` | #106 | Live ride heights and CFD aero moments in DynPy. BobSim's own ride-height references change. |
| `95253a2` | #110 | BobLib `1bd6d78`: bellcrank visual pickups. Equation counts change. |

The other 22 commits change layout, build, docs, reports, cache identity
or the app. `9e463c8` (#80) can evict a stale lap GGV cache. That can
change a lap result without a physics change.

### Aero ride-height references

Our `vehicle/vehicle.yml` did not take the ride-height references from
#106. Its values need a named source before they change.

- Our references are BobSim's values from before #106. They are the
  wheel centers, with z = 0.1999 m.
- That is above both CFD grids. The front grid covers 0.03556 m to
  0.10668 m. The rear grid covers 0.04191 m to 0.12573 m.
- So aero from our points clamps at the grid corner. It does not change
  with ride height or pitch.
- The Modelica tier reads these points after `make records`. It clamped
  in the same way at `4da577a`. A Modelica result at speed for our car
  uses the corner of the map.
- At `fe03bab`, DynPy, GGV and the lap read these points too. At
  `4da577a`, DynPy used a fixed 76.2 mm ride height. No lhre target or
  study runs DynPy, GGV or the lap on our file today.
- Treat an aero-dependent DynPy, GGV or lap result for our car as invalid
  until a named source gives chassis ride-height points.
- BobLib's record for BobSim's car holds chassis points for the same aero
  map. A vehicle change that uses them for our car needs a named source
  that says they fit our chassis.

When the points move inside the grids, remove this section.

## Windows

- `make init` checks out the submodules with LF endings and long paths on.
  A CRLF checkout makes BobSim report every generated record as stale.
- Do not clone lhre with `--recurse-submodules` on Windows. If you did,
  run `git submodule deinit -f simulation/bobsim`, then `make init`.
