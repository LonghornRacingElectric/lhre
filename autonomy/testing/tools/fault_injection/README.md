# Fault injection tools

Proves in Gazebo that each `lhr_safety` check catches its fault mid-drive and
stops the car, and measures how fast
([#117](https://github.com/LonghornRacingElectric/lhre/issues/117)). Each
stop is scored against the PDR formula d = v(T + 0.2 s) + v²/2a, with T the
measured time to trip and a the spring brake's 6 m/s² floor. Clean laps
count false trips.

## Files

| File | What it does |
|---|---|
| `run_faults.sh` | Runs the matrix: every case at every speed, `REPEATS` times, plus clean autocross laps. One headless launch per run, recorded to a bag |
| `analyze_run.py` | Scores one bag and appends a row to `results.csv` |
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
| `steer_freeze` | The actuators keep the steering angle from the fault on; `safety_node` still sees the real command | `STEER_TRACKING` |
| `geofence` | No injection: the fence shrinks to `FENCE` m (20) | `GEOFENCE` |
| `clean` | None, on autocross seeds | Nothing |

## Running

From `autonomy/ros2`, after `./scripts/build.sh`, on Ubuntu or in the Docker
container:

```bash
../testing/tools/fault_injection/run_faults.sh
```

Environment variables pick the matrix: `CASES`, `SPEEDS` (`v_max` values,
default `3 5 8`), `REPEATS` (2), `CLEAN_SEEDS` (`1 2 3 4 5`, empty for none),
`AFTER` (fault seconds after `DRIVING`, 25), `RUN_S` (60), `CLEAN_S` (150),
`OUT` and `EXTRA` (more launch arguments). The full matrix is about 60 runs,
roughly 2.5 hours. A quick check:

```bash
CASES="ekf_kill steer_freeze" SPEEDS=5 REPEATS=1 CLEAN_SEEDS= \
    ../testing/tools/fault_injection/run_faults.sh
```

On a CPU-rendered machine (the Mac container) Gazebo drops LiDAR frames, so
add `EXTRA="safety_lidar_timeout_s:=1.5"` or every run trips `LIDAR_STALE`
first ([#123](https://github.com/LonghornRacingElectric/lhre/issues/123)).

Output goes to `~/lhr-test-data/<date>-fault-injection/`: `bags/` (one bag
plus launch and recorder logs per run), `results.csv`, `summary.md`. Bags
never go in git; the campaign folder under `autonomy/testing/` gets
`results.csv`, the summary and the chosen figures, and the bags go to
SharePoint ([testing/README.md](../../README.md)).

## Reading the results

- `match`: the check that tripped is the one the fault should trip. A
  different check tripping first is a finding, not noise.
- `trip_latency_s`: fault to latched trip, in sim time.
- `stop_dist_m`: path length from the fault to standstill, from ground
  truth. Stops are optimistic until the regen and spring-brake model lands
  (the rest of [#116](https://github.com/LonghornRacingElectric/lhre/issues/116)):
  the sim stops the wheels outright.
- `within_pdr`: the stop fits the formula with the measured latency.
- `false_trip`: a clean lap tripped anything.

Any run with `match` or `within_pdr` false, or a false trip, gets an issue.
