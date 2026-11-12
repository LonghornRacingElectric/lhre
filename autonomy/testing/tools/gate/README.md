# Gate tools

Runs the 10/28 Software MVP gate
([#109](https://github.com/LonghornRacingElectric/lhre/issues/109)) in Gazebo
and scores every run against the
[pass criteria](../../../docs/plans/mvp-pass-criteria.md), so the gate is a
table of passes and failures rather than a judgment call. The runner now
selects the Mid-360 and camera-fusion sensor stack for
[#206](https://github.com/LonghornRacingElectric/lhre/issues/206).
It uses the MVP matrix with course-derived reference-lap scoring; the expanded
matrix and FMU checks in the [Sim v2 criteria](../../../docs/plans/sim-v2-pass-criteria.md)
require the remaining gate work.

## Files

| File | What it does |
|---|---|
| `run_gate.sh` | Runs the matrix: autocross seeds 1 to 5 three times each, acceleration three times and skidpad three times on the no-shortcuts stack. One headless launch per run, recorded to a bag |
| `score_gate.py` | Reads every run folder and writes `results.csv` (one row per run) and `summary.md` (the pass/fail table). Rerunnable, it only reads |
| `watch_run.py` | Ends each run in sim time and writes how it ended to `end.txt`. The fault tools use it too |

Every gate run is `perception:=lidar estimator:=ekf safety:=true
lidar_source:=mid360 enable_stereo_camera:=true enable_camera_lidar_fusion:=true`.
Sensor selection belongs to the runner; standalone Gazebo launch defaults
remain unchanged. Fusion supplies `/lhr/perception/cones_classified` to the
planner and enables LiDAR side inference for camera fallback.
Optional baselines use `perception:=sim estimator:=truth lidar_source:=gazebo`,
with stereo and fusion explicitly disabled, for comparison only. Lap time is
scored against a [reference lap](#the-reference-lap) computed from the course.
A run ends `SETTLE_S` sim seconds after the mission reaches `FINISHED` or
`EMERGENCY`, so the bag holds the stop, or at `RUN_S` sim seconds.
Skidpad joins the matrix on its own once
`lhr_gazebo/worlds/skidpad_seed1.sdf` is in the tree
([#114](https://github.com/LonghornRacingElectric/lhre/issues/114)). Until
then the runner writes it to `waived.txt` and the summary names it as
waived, as the MVP pass criteria allow, instead of failing three missing runs.
That waiver does not satisfy #206 or Sim v2, which both require skidpad.
Skidpad scores like acceleration, so the skidpad work has to meet the same
contract: `track_style:=skidpad` runs the mission, the mission manager
publishes `/lhr/mission/complete` when the car crosses the exit line (not
once it has stopped, or the stop distance reads zero), and metrics reports
`mission_finished` against the skidpad reference. Metrics has no skidpad
reference yet, so skidpad runs fail `completes` until it does.

## Running

From `autonomy/ros2`, after `./scripts/build.sh`, on Ubuntu or in the Docker
container:

```bash
../testing/tools/gate/run_gate.sh
```

Environment variables pick the matrix: `SEEDS` (`1 2 3 4 5`), `REPEATS` (3),
`ACCEL_REPEATS` and `SKIDPAD_REPEATS` (3 each, empty or 0 for none),
`BASELINE` (0; 1 adds one truth-stack run per seed, for comparison only),
`RUN_S` (180 sim seconds), `SETTLE_S` (5 sim seconds), `HANG_S` (60 wall
seconds), `OUT` and `EXTRA` (more launch arguments, split on whitespace).
The runner's per-run arguments follow `EXTRA`, so gate sensor selection and
baseline exclusions take precedence. The full matrix without skidpad is
18 runs. Historical MVP timing predates the combined Mid-360/camera stack.
A quick check:

```bash
SEEDS=1 REPEATS=1 ACCEL_REPEATS=1 SKIDPAD_REPEATS=1 ../testing/tools/gate/run_gate.sh
```

On a CPU-rendered machine (the Mac container) Gazebo still drops LiDAR
frames, with gaps up to about 0.7 s even with the cameras off (#160), so
add `EXTRA="safety_lidar_timeout_s:=1.5"` or runs trip `LIDAR_STALE`.
Say so next to the results, since it is not a launch default.

### Sim time, not wall time

Every duration that decides a result is in sim seconds
([#227](https://github.com/LonghornRacingElectric/lhre/issues/227)), so a
machine that runs the sim at one sixth of real time (the FMU plant on
red-mesa) runs the same campaign and gets the same answer, only six times
later. `watch_run.py` reads `/clock` and the mission state and ends the run
at `SETTLE_S` after `FINISHED` or `EMERGENCY`, at the `RUN_S` cap, or when
the launch exits on its own.

Wall time is only a hang guard: if `/clock` stands still for `HANG_S`
seconds, counting from launch so a sim that never starts counts too, the
run ends as hung and fails `health`. Raise `HANG_S` only if startup on a
machine takes longer than a minute. `summary.md` reports the sim speed each
campaign ran at, which says how long it took and nothing about the result.

Wall clocks left in the path, and why they stay:

- The cleanup waits between runs (process exit, `sleep 2`). They separate
  runs and never touch a scored run.
- `lhr_demo`'s driving study runner (`--wall-timeout`) is not part of the
  gate.

Output goes to `~/lhr-test-data/<date>-mid360-fusion-gate/`: `runs/<id>/` (bag,
`metrics.csv`, `end.txt`, launch and recorder logs, the complete ROS launch
command in `args.txt`, one argument per line), then
`results.csv` and `summary.md`. `expected.txt` lists every planned run, and
the scorer fails any planned run with no result, so a campaign cut short
(a laptop sleeping, an SSH drop) never reads as a pass. Rerunning resumes:
a finished run (its folder has `args.txt`, written last) is skipped, a run
that was cut short is moved to `incomplete/` and run again, and Ctrl-C stops
the current run cleanly. Move a finished folder aside to rerun it.
Always choose a fresh `OUT` after changing code or settings: resume checks
the completion marker, not whether the saved settings match the new run.
Old MVP folders and their evidence remain historical.
Bags never go in git. A new campaign's write-up goes in
`autonomy/testing/<date>-mid360-fusion-gate/` with `results.csv`, the summary and
the chosen figures, and the bags go to SharePoint
([testing/README.md](../../README.md)).

## Sensor-rate evidence

Run one full stack at a time on red-mesa for official acceptance, with the
default 0.3 s LiDAR timeout. Mac Docker runs are development evidence only,
including any run with a relaxed timeout. While the car is driving, observe
these three topics with the installed ROS tools, saving their output beside
the campaign's raw data:

```bash
ros2 topic hz --wall-time /lhr/lidar/points
ros2 topic hz --wall-time /lhr/sensor/zed2i/left/image_rect
ros2 topic hz --wall-time /lhr/sensor/zed2i/right/image_rect
```

Use separate terminals so their observation intervals overlap. `ros2 topic
info --verbose <topic>` shows publisher/subscriber QoS if a monitor receives
no samples. Record each observed mean rate, maximum interval, window size,
observation duration and whether the window covers driving. These are
subscriber-observed wall-time rates, not configured sensor rates. Record
real-time factor from the change in `/clock` over elapsed wall time too;
wall-time intervals cannot be compared directly with a ROS-time safety
timeout. Missing samples are missing evidence, not a zero rate or a pass.

The campaign README records the commit (and any uncommitted diff), machine,
plant, actual arguments, rate measurements and scoring results. Keep the
camera interruption/recovery check separate from the clean course matrix;
inspect `/lhr/perception/cones_classified` and the centerline before loss,
after `fusion_camera_max_age_sec` and after restoration. Physical colors on
`/lhr/perception/cones_fused` may persist while the planner uses LiDAR sides.
The [#85](https://github.com/LonghornRacingElectric/lhre/issues/85) and
[#86](https://github.com/LonghornRacingElectric/lhre/issues/86) fallback
failure mechanisms, regression fixes and development driving checks are
recorded in the [side-classifier validation](../../2026-11-08-side-classifier-fixes/README.md).
Follow the
[testing rules](../../README.md) for storing and verifying raw recordings.

## Reading the results

Each gate run gets a pass or fail per check:

| Check | Pass when | Source |
|---|---|---|
| `completes` | metrics `outcome` is `lap` (autocross) or `mission_finished` (acceleration, skidpad) | metrics |
| `stops` | standstill within 30 m (autocross), 75 m (acceleration) or 25 m (skidpad) of the finish, mission `FINISHED`, and the car never rolls above 0.5 m/s again | bag |
| `off_course` | `off_track_count` is 0 | metrics (#156) |
| `cones` | `cones_hit` is 0 | metrics (#156) |
| `cte` | `max_cte` at most 1.0 m | metrics |
| `ekf` | `max_pos_error` at most 0.5 m | metrics |
| `lap_time` | at most 1.5 times the course's reference lap; always passes for acceleration and skidpad | metrics, world |
| `health` | no node other than metrics dies or exits before teardown, no `EMERGENCY`, and the sim did not hang | launch log, bag, `end.txt` |

The finish is the reference gate for autocross (`/lhr/metrics/lap_complete`)
and the mission's own completion for acceleration and skidpad
(`/lhr/mission/complete`, the car on the finish line). Stop distances are optimistic until the regen and
spring-brake model lands
([#199](https://github.com/LonghornRacingElectric/lhre/issues/199)): the sim
stops the wheels outright.

The gate passes only when every run passes. Per the pass criteria, a failed
run gets an issue in the milestone and is repeated after the fix, never
dropped.

### The reference lap

`lap_time` compares the lap with the fastest standing-start lap of the
world's reference centerline under the run's own speed limits: `v_max`,
`a_lat_max` (floored at `v_min`, as the planner is), `max_accel` and
`max_decel` ([#232](https://github.com/LonghornRacingElectric/lhre/issues/232)).
`lhr_metrics/reference_lap.py` computes it from the world and the limits in
the run's `metrics.csv`, starting at rest on the start/finish gate. It
depends only on the course and those numbers, so it is the same on every
machine. It replaced the truth-stack baseline, which laps 11 to 20% slower
on red-mesa than on the Mac and fails outright on some rules-compliant
seeds.

On every lap recorded on red-mesa through 11/8, the no-shortcuts stack
lapped at 0.99 to 1.00 times the reference on the Gazebo plant (autocross
seeds 1 to 5 and rules-compliant seeds 1 to 3) and 1.04 on the FMU plant.
The car can beat the reference slightly because pure pursuit cuts inside
the centerline. The truth stack lapped at 1.10 to 1.19, and 1.70 on
rules-compliant seed 1. So 1.5 leaves room for honest variation and still
catches a car that crawls or stalls.

`results.csv` gives `reference_lap_s` and `lap_reference` (`course`) for each
autocross run, and the summary lists each course's reference. A campaign
recorded before the Gazebo launch logged the speed limits to metrics falls
back to its truth-stack baselines (`lap_reference` reads `truth baseline`).

## Rules-compliant autocross campaigns

Set `TRACK_STYLE=rules_autocross` to replace the autocross rows with the new
seeded course style. The runner generates each matching world under
`$OUT/worlds` and passes it explicitly to launch, and the scorer reads the
reference lap from that world. Default `TRACK_STYLE=autocross` preserves the
MVP matrix and its layouts. Run IDs and the results CSV's `track_style`
distinguish the styles.
Use a separate `OUT` for each campaign. For example, from `autonomy/ros2`:

```bash
TRACK_STYLE=rules_autocross SEEDS=1 REPEATS=1 ACCEL_REPEATS=0 SKIDPAD_REPEATS=0 \
  OUT="$HOME/lhr-test-data/rules-smoke" ../testing/tools/gate/run_gate.sh
```

The runner uses default width/spacing; for custom dimensions generate a world
and run the launch directly as described in
[lhr_trackgen](../../../ros2/src/lhr_trackgen/README.md#rules-compliant-autocross).
`RUN_S` is sim seconds, so a slow machine needs no change; raise it only for
a course whose lap takes longer than 180 sim seconds.
These runs still use the existing MVP scorer thresholds; inclusion and changed
thresholds in the official Sim v2 gate belong to
[#201](https://github.com/LonghornRacingElectric/lhre/issues/201).

## Imported competition layouts

`REAL_TRACKS="amz_fsg amz_fsi"` appends fixed imported circuits, each with its
own seed-1 truth baseline and `REPEATS` LiDAR/EKF runs. The default is empty.
Imports are verified before output creation or process cleanup. Flagged
layouts require `ALLOW_NONCOMPLIANT=1`, which forwards the launch's explicit
acknowledgement. See [sources and measured flags](../../../ros2/src/lhr_gazebo/worlds/competition/README.md).

From the built, sourced ROS workspace, preview an imports-only matrix safely:

```bash
SEEDS="" ACCEL_REPEATS=0 SKIDPAD_REPEATS=0 REPEATS=1   REAL_TRACKS="amz_fsg amz_fsi" ALLOW_NONCOMPLIANT=1 PLAN_ONLY=1   ../testing/tools/gate/run_gate.sh
```

Remove `PLAN_ONLY=1` to run it; choose a fresh `OUT` for a new campaign.
FSG needs an explicit `EXTRA="max_step_m:=8"` to chain its sparse original
gates with the current planner. Record this override beside results; importing
a layout does not change controller or planner defaults.
`PLAN_ONLY` prints the audited matrix without launching or stopping processes.
The runner saves `course_audits.json`; the scorer includes flags in the
summary and matches lap baselines by layout **and** seed. Missing planned
import runs fail. These are diagnostic additions; official v2 adoption is
owned by [#201](https://github.com/LonghornRacingElectric/lhre/issues/201).
Neither existing pass thresholds nor the default MVP matrix changes.
