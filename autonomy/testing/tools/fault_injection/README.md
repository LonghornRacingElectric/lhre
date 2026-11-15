# Fault injection tools

Proves in Gazebo that each `lhr_safety` check catches its fault mid-drive and
stops the car, and measures how fast
([#117](https://github.com/LonghornRacingElectric/lhre/issues/117)). Each
stop is scored against the PDR formula d = v(T + 0.2 s) + v²/2a, with T the
measured time to trip and a the spring brake's 6 m/s² floor. Clean laps
count false trips. Every run launches with the gate stack in
[`stack.args`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/testing/tools/gate/stack.args), the same file the
[gate runner](../gate/README.md) reads
([#237](https://github.com/LonghornRacingElectric/lhre/issues/237)), so the
fault matrix runs the Mid-360 caster and camera fusion like the gate. Matrices
from before #237 ran the Gazebo GPU LiDAR without cameras, so their numbers
are not directly comparable.

## Files

| File | What it does |
|---|---|
| `run_faults.sh` | Runs the matrix: every case at every speed, `REPEATS` times, plus clean autocross laps. One headless launch per run, recorded to a bag and ended in sim time by the gate's `watch_run.py` |
| `analyze_run.py` | Scores one bag and writes its row to `results.csv`, replacing an earlier run with the same id |
| `summarize.py` | Turns `results.csv` into the markdown table for the campaign README |

The faults themselves come from `lhr_gazebo`'s `fault_injector`
(`fault:=<kind>` on the Gazebo launch); see
[lhr_gazebo's README](../../../ros2/src/lhr_gazebo/README.md#fault-injection).

| Case | Fault | Should trip |
|---|---|---|
| `ekf_kill` | Kill `ekf_node` | `ODOM_STALE` |
| `controller_kill` | Kill `pure_pursuit` | `CMD_STALE` |
| `controller_pause` | Freeze `pure_pursuit` for 3 s | `CMD_STALE` |
| `odom_nan` | NaN in the estimated pose from then on | `EKF_DIVERGED` |
| `odom_spike` | One estimated-odometry sample at 410 m/s | `EKF_DIVERGED` |
| `lidar_drop` | Stop forwarding the point cloud | `LIDAR_STALE` |
| `cmd_nan` | NaN steering in every command | `CMD_INVALID` |
| `steer_freeze` | The actuators keep the steering angle from the fault on; `safety_node` still sees the real command, and the measured angle stops following it | `STEER_TRACKING` |
| `imu_drop` | Stop forwarding the IMU; the EKF keeps publishing fresh odometry ([#126](https://github.com/LonghornRacingElectric/lhre/issues/126)) | `IMU_STALE` |
| `wheel_drop` | Stop forwarding the wheel speeds, likewise | `WHEEL_STALE` |
| `geofence` | No injection: the fence shrinks to `FENCE` m (20) | `GEOFENCE` |
| `clean` | None, on autocross seeds (a seed with no committed world gets one generated into `$OUT/worlds`) | Nothing |

`mission_kill` (kill `mission_manager`, should trip `MISSION_STALE`) is a
fault kind but not a default case: nothing in sim stops the car after it,
since the stop goes through the dead node and the car's hardware loop is not
modelled, so every run would fail the stop check. `CASES=mission_kill` shows
the trip. It joins the matrix with
[#290](https://github.com/LonghornRacingElectric/lhre/issues/290).

## Running

From `autonomy/ros2`, after `./scripts/build.sh`, on Ubuntu or in the Docker
container:

```bash
../testing/tools/fault_injection/run_faults.sh
```

Environment variables pick the matrix: `CASES` (empty for none), `SPEEDS` (`v_max` values,
default `3 5 8`), `REPEATS` (2), `TRACK` (for the fault cases, `oval`),
`CLEAN_SEEDS` (`1 2 3 4 5`, empty for none), `AFTER` (fault seconds after
`DRIVING`, 25), `AT_SPEED` (0.95: the fault also waits for this share of
`v_max`; 0 to 1, 0 fires on time alone, anything else stops the runner), `RUN_S` (60), `CLEAN_S` (150), `HANG_S` (60), `OUT` and
`EXTRA` (more launch arguments; the gate stack and each case's own arguments
follow it and take precedence). `AFTER`, `RUN_S` and `CLEAN_S` are sim
seconds, so a machine that runs below real time gets the same runs, only
later ([#227](https://github.com/LonghornRacingElectric/lhre/issues/227)).
`HANG_S` is the one wall-time setting: a run whose `/clock` stands still that
long ends as `hung` (the gate's
[`watch_run.py`](../gate/README.md#sim-time-not-wall-time) does this for
both tools, and writes `bags/<id>.end.txt`). The full matrix is 71 runs; the
59 it had on 11/1 took 71 minutes in the Mac container. A quick check:

```bash
CASES="ekf_kill steer_freeze" SPEEDS=5 REPEATS=1 CLEAN_SEEDS= \
    ../testing/tools/fault_injection/run_faults.sh
```

On a CPU-rendered machine (the Mac container) Gazebo still drops LiDAR
frames, with gaps up to about 0.7 s even with the cameras off (#160), so
add `EXTRA="safety_lidar_timeout_s:=1.5"` or every run trips `LIDAR_STALE`
first.

## What each tier tests

A fault fires `AFTER` sim seconds into `DRIVING` and once the car is doing
`AT_SPEED` times its tier's `v_max`, read from the plant's own odometry
(`fault_min_speed_mps` on the Gazebo launch). A time alone does not test a
speed: the oval is an ellipse whose tight ends (9 m radius) cap the car near
5.2 m/s at the default `a_lat_max` of 3.0, and on 11/1 every 8 m/s run hit
25 s in a tight end, at 5.3 m/s
([#207](https://github.com/LonghornRacingElectric/lhre/issues/207)). The flat
sides (42 m radius) let it reach 8 m/s for about 71 m of each 128 m lap, so
the 8 m/s tier now faults there, at most one lap after `AFTER`.

| Tier | What it tests |
|---|---|
| 3 and 5 m/s | The whole oval is at `v_max`, so the fault fires at `AFTER` |
| 8 m/s | The fault waits for the flat side, up to one lap (about 24 s) after `AFTER`, still inside `RUN_S` |
| `geofence` (every tier) | Not an injection: the car trips where it crosses the `FENCE` radius, at whatever speed it has there (6.3 m/s on the 8 m/s tier on 11/1) |

`results.csv` marks each injected run `at_speed` (`v_at_fault` at least 90% of
`v_max`, missed faults included), the summary table gives the median speed at
the fault, and the summary names every injected run below that, so a tier that
did not run at its speed cannot pass for one that did. `geofence` is left
blank: where it trips is set by the fence, not the tier.

Every run launches with `enable_metrics:=false`. Since #132 a finished lap
ends the mission, and `safety_node` only arms in `READY` and `DRIVING`, so
a fault after the first lap would score `missed`. An 8 m/s oval lap takes
about 24 s, under `AFTER`. The clean laps then keep driving for all of
`CLEAN_S`, which gives false trips more time to show.

Output goes to `~/lhr-test-data/<date>-fault-injection/`: `bags/` (one bag
plus launch and recorder logs per run), `worlds/` (generated seeds),
`results.csv`, `summary.md`. Bags
never go in git; the campaign folder under `autonomy/testing/` gets
`results.csv`, the summary and the chosen figures, and the bags go to
SharePoint ([testing/README.md](../../README.md)).

## Reading the results

- `result`: what the run shows.
  - `caught`: the expected check tripped after the fault.
  - `wrong_check`: a different check tripped first. That's a finding, not
    noise.
  - `missed`: the fault ran and nothing tripped.
  - `early_trip`: something tripped before the fault, which is a false trip.
    The geofence case is the exception: the fence trips on the estimate,
    which can cross before the truth does, so an early `GEOFENCE` counts and
    its latency is negative.
  - `clean` and `false_trip`: a clean lap without or with a trip.
  - `no_drive` (never reached `DRIVING`, or covered under 10 m before the
    fault), `no_fault` (the fault never started), `hung` (the sim
    clock stopped) and `error` (the bag did not read): the run itself went
    wrong. These are listed under the
    summary table, not counted in it. Rerun them, but first check a
    `no_fault` run's launch log: if `fault_injector` logged that the fault
    waits for a speed, the car never reached the gate on that track, and
    only a track or `AT_SPEED` the tier can reach fixes it, not a longer
    `RUN_S`.
- `at_speed`: `v_at_fault` was at least 90% of `v_max`, so the run tested
  its tier (blank when either is unknown, and for `geofence`).
- `trip_latency_s`: fault to latched trip, in sim time.
- `visible_after_s` and `latency_from_visible_s` (`steer_freeze` only): a
  frozen actuator is invisible until the command moves
  `steer_angle_tol_rad` off the frozen angle, so these split the latency at
  that moment. A trip before the freeze shows scores 0, and a freeze that
  never shows reads `never` with 0. The `STEER_TRACKING` budget in the Sim
  v2 pass criteria applies to `latency_from_visible_s`
  ([#208](https://github.com/LonghornRacingElectric/lhre/issues/208)); the
  summary prints both.
- `stop_dist_m`: path length from the fault to standstill, from ground
  truth. Stops are optimistic until the regen and spring-brake model lands
  ([#199](https://github.com/LonghornRacingElectric/lhre/issues/199)):
  the sim stops the wheels outright.
- `within_pdr`: the stop fits the formula with the measured latency, and
  the car stayed stopped (`max_v_after_stop` at most 0.5 m/s). A run that
  never stops is false.
- `drove_m`: distance covered in `DRIVING` before the fault or trip.

Any run that is not `caught` (or `clean`), or has `within_pdr` false, gets
an issue.

## Rules-compliant courses

`TRACK=rules_autocross` runs fault cases on the new generated course style.
`CLEAN_TRACK=rules_autocross` selects it for clean laps too (default remains
`autocross`). Missing worlds are generated with the shared generator into
`$OUT/worlds`; the analyzer records the selected style. For example:

```bash
TRACK=rules_autocross CLEAN_TRACK=rules_autocross CASES=lidar_drop SPEEDS=5 \
  REPEATS=1 CLEAN_SEEDS=1 CLEAN_S=600 OUT="$HOME/lhr-test-data/rules-faults" \
  ../testing/tools/fault_injection/run_faults.sh
```

Use a separate output directory when changing styles, since fault/clean run
IDs retain their existing format. The courses are longer than MVP autocross,
so give clean runs enough sim time (`CLEAN_S`). Their geometry checks do not guarantee
the controller can complete them. See
[the course contract](../../../ros2/src/lhr_trackgen/README.md#rules-compliant-autocross).
