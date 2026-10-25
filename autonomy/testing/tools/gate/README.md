# MVP gate tools

Runs the 10/28 Software MVP gate
([#109](https://github.com/LonghornRacingElectric/lhre/issues/109)) in Gazebo
and scores every run against the
[pass criteria](../../../docs/plans/mvp-pass-criteria.md), so the gate is a
table of passes and failures rather than a judgment call.

## Files

| File | What it does |
|---|---|
| `run_gate.sh` | Runs the matrix: autocross seeds 1 to 5 three times each and acceleration three times on the no-shortcuts stack, plus one truth-stack baseline per seed. One headless launch per run, recorded to a bag |
| `score_gate.py` | Reads every run folder and writes `results.csv` (one row per run) and `summary.md` (the pass/fail table). Rerunnable, it only reads |

Every gate run is `perception:=lidar estimator:=ekf safety:=true` with launch
defaults, which is the stack the pass criteria define. The baselines are
`perception:=sim estimator:=truth`, the reference for the lap time check.
A run ends `SETTLE_S` seconds after the mission reaches `FINISHED` or
`EMERGENCY`, so the bag holds the stop, or at `RUN_S` wall seconds.
Skidpad is not in the matrix yet: it has no track style on main.

## Running

From `autonomy/ros2`, after `./scripts/build.sh`, on Ubuntu or in the Docker
container:

```bash
../testing/tools/gate/run_gate.sh
```

Environment variables pick the matrix: `SEEDS` (`1 2 3 4 5`), `REPEATS` (3),
`ACCEL_REPEATS` (3, empty or 0 for none), `BASELINE` (1, 0 to skip the
truth-stack runs), `RUN_S` (180), `SETTLE_S` (3), `OUT` and `EXTRA` (more
launch arguments). The full matrix is 23 runs, about an hour on the Mac
container. A quick check:

```bash
SEEDS=1 REPEATS=1 ACCEL_REPEATS=1 ../testing/tools/gate/run_gate.sh
```

On a CPU-rendered machine (the Mac container) Gazebo drops LiDAR frames, so
add `EXTRA="safety_lidar_timeout_s:=1.5"` or runs trip `LIDAR_STALE`
([#123](https://github.com/LonghornRacingElectric/lhre/issues/123)). Say so
next to the results, since it is not a launch default.

Output goes to `~/lhr-test-data/<date>-mvp-gate/`: `runs/<id>/` (bag,
`metrics.csv`, launch and recorder logs, the launch arguments), then
`results.csv` and `summary.md`. `expected.txt` lists every planned run, and
the scorer fails any planned run with no result, so a campaign cut short
(a laptop sleeping, an SSH drop) never reads as a pass. Rerunning resumes:
a finished run (its folder has `args.txt`, written last) is skipped, a run
that was cut short is moved to `incomplete/` and run again, and Ctrl-C stops
the current run cleanly. Move a finished folder aside to rerun it.
Bags never go in git. The gate's write-up goes in
`autonomy/testing/2026-10-28-mvp-gate/` with `results.csv`, the summary and
the chosen figures, and the bags go to SharePoint
([testing/README.md](../../README.md)).

## Reading the results

Each gate run gets a pass or fail per check:

| Check | Pass when | Source |
|---|---|---|
| `completes` | metrics `outcome` is `lap` (autocross) or `mission_finished` (acceleration) | metrics |
| `stops` | standstill within 30 m (autocross) or 75 m (acceleration) of the finish, mission `FINISHED`, and the car never rolls above 0.5 m/s again | bag |
| `off_course` | `off_track_count` is 0 | metrics (#156) |
| `cones` | `cones_hit` is 0 | metrics (#156) |
| `cte` | `max_cte` at most 1.0 m | metrics |
| `ekf` | `max_pos_error` at most 0.5 m | metrics |
| `lap_time` | at most 1.5 times the same seed's baseline; always passes for acceleration | metrics |
| `health` | no node other than metrics dies or exits before teardown, and no `EMERGENCY` | launch log, bag |

The finish is the reference gate for autocross (`/lhr/metrics/lap_complete`)
and the mission's own completion for acceleration (`/lhr/mission/complete`,
the nose on the line). Stop distances are optimistic until the regen and
spring-brake model lands
([#116](https://github.com/LonghornRacingElectric/lhre/issues/116)): the sim
stops the wheels outright.

The gate passes only when every run passes. Per the pass criteria, a failed
run gets an issue in the milestone and is repeated after the fix, never
dropped.
