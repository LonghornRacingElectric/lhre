# Gate tools

Runs the 10/28 Software MVP gate
([#109](https://github.com/LonghornRacingElectric/lhre/issues/109)) in Gazebo
and scores every run against the
[pass criteria](../../../docs/plans/mvp-pass-criteria.md), so the gate is a
table of passes and failures rather than a judgment call. The runner now
selects the Mid-360 and camera-fusion sensor stack for
[#206](https://github.com/LonghornRacingElectric/lhre/issues/206).
It still uses the MVP matrix and scorer; the expanded matrix, course-derived
lap reference and FMU checks in the [Sim v2 criteria](../../../docs/plans/sim-v2-pass-criteria.md)
require the remaining gate work.

## Files

| File | What it does |
|---|---|
| `run_gate.sh` | Runs the matrix: autocross seeds 1 to 5 three times each, acceleration three times and skidpad three times on the no-shortcuts stack, plus one truth-stack baseline per autocross seed. One headless launch per run, recorded to a bag |
| `score_gate.py` | Reads every run folder and writes `results.csv` (one row per run) and `summary.md` (the pass/fail table). Rerunnable, it only reads |

Every gate run is `perception:=lidar estimator:=ekf safety:=true
lidar_source:=mid360 enable_stereo_camera:=true enable_camera_lidar_fusion:=true`.
Sensor selection belongs to the runner; standalone Gazebo launch defaults
remain unchanged. Fusion supplies `/lhr/perception/cones_classified` to the
planner and enables LiDAR side inference for camera fallback.
The baselines use `perception:=sim estimator:=truth lidar_source:=gazebo`,
with stereo and fusion explicitly disabled, as the lap-time reference.
A run ends `SETTLE_S` seconds after the mission reaches `FINISHED` or
`EMERGENCY`, so the bag holds the stop, or at `RUN_S` wall seconds.
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
`BASELINE` (1, 0 to skip the
truth-stack runs), `RUN_S` (180), `SETTLE_S` (3), `OUT` and `EXTRA` (more
launch arguments, split on whitespace). The runner's per-run arguments follow
`EXTRA`, so gate sensor selection and baseline exclusions take precedence.
The full matrix without skidpad is 23 runs. Historical MVP timing predates
the combined Mid-360/camera stack. A quick check:

```bash
SEEDS=1 REPEATS=1 ACCEL_REPEATS=1 SKIDPAD_REPEATS=1 ../testing/tools/gate/run_gate.sh
```

On a CPU-rendered machine (the Mac container) Gazebo still drops LiDAR
frames, with gaps up to about 0.7 s even with the cameras off (#160), so
add `EXTRA="safety_lidar_timeout_s:=1.5"` or runs trip `LIDAR_STALE`
Say so next to the results, since it is not a launch default.

Output goes to `~/lhr-test-data/<date>-mid360-fusion-gate/`: `runs/<id>/` (bag,
`metrics.csv`, launch and recorder logs, the complete ROS launch command in
`args.txt`, one argument per line), then
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
| `lap_time` | at most 1.5 times the same seed's baseline; always passes for acceleration and skidpad | metrics |
| `health` | no node other than metrics dies or exits before teardown, and no `EMERGENCY` | launch log, bag |

The finish is the reference gate for autocross (`/lhr/metrics/lap_complete`)
and the mission's own completion for acceleration and skidpad
(`/lhr/mission/complete`, the car on the finish line). Stop distances are optimistic until the regen and
spring-brake model lands
([#199](https://github.com/LonghornRacingElectric/lhre/issues/199)): the sim
stops the wheels outright.

The gate passes only when every run passes. Per the pass criteria, a failed
run gets an issue in the milestone and is repeated after the fix, never
dropped.

## Rules-compliant autocross campaigns

Set `TRACK_STYLE=rules_autocross` to replace the autocross rows and their
truth baselines with the new seeded course style. The runner generates each
matching world under `$OUT/worlds` and passes it explicitly to launch. Default
`TRACK_STYLE=autocross` preserves the MVP matrix and its layouts. Run IDs and
the results CSV's `track_style` distinguish the styles; baseline matching uses
both style and seed, so a short MVP baseline cannot score a longer course.
Use a separate `OUT` for each campaign. For example, from `autonomy/ros2`:

```bash
TRACK_STYLE=rules_autocross SEEDS=1 REPEATS=1 ACCEL_REPEATS=0 SKIDPAD_REPEATS=0 \
  RUN_S=600 OUT="$HOME/lhr-test-data/rules-smoke" ../testing/tools/gate/run_gate.sh
```

The runner uses default width/spacing; for custom dimensions generate a world
and run the launch directly as described in
[lhr_trackgen](../../../ros2/src/lhr_trackgen/README.md#rules-compliant-autocross).
Longer courses need a larger wall timeout, particularly with CPU rendering.
These runs still use the existing MVP scorer thresholds; inclusion and changed
thresholds in the official Sim v2 gate belong to
[#201](https://github.com/LonghornRacingElectric/lhre/issues/201).
