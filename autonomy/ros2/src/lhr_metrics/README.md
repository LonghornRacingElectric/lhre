# lhr_metrics

Measures a run: cross-track error against the published centerline,
distance spent off track, speed stats, and lap completion. Writes one CSV
row per run to `data/metrics.csv` (never committed, see
`autonomy/AGENTS.md`).

## What a row holds

39 columns in six groups, because a row of results alone cannot be
compared with another row: nothing in it says what differed between the
two runs that produced them.

| Group | Columns |
| --- | --- |
| Identity | `run_id` |
| Provenance | `vehicle_sha256`, `git_sha`, `scenario` |
| Scenario | `seed`, `track_style`, `num_waypoints`, `mission`, `perception`, `start_on_track`, `start_finish_cones` |
| Sensor | `lidar`, `mount_pitch_rad`, `elevation_profile`, `return_profile`, `stack_window_sec`, `min_cluster_points`, `ground_z_min`, `fov_deg`, `max_range_m`, `noise_std_m`, `false_negative_rate` |
| Control | `lookahead_dist`, `a_lat_max`, `v_min`, `v_max`, `max_accel`, `max_decel` |
| Outcomes | `outcome`, `duration_s`, `samples`, `path_length_m`, `mean_cte`, `max_cte`, `off_track_count`, `off_track_dist_m`, `mean_speed`, `max_speed`, `lap_completed` |

`git_sha` comes from the launch file and carries a `-dirty` suffix when the
tree has uncommitted changes, since a clean-looking id on a modified tree
is worse than no id. `vehicle_sha256` hashes `lhr_vehicle`'s config file
with the same formula the study harness in `simulation/tools` uses, so a
sim run and a BobDyn study are comparable on that field.

The independent variables are declared in `RUN_PARAMS` and passed in by
the launch file. **A new launch argument that changes a result belongs in
that tuple too**, or rows quietly stop explaining themselves.

A `data/metrics.csv` written under an older column set is moved aside to
`data/metrics.csv.old` with a warning rather than having its extra fields
dropped, which is how a metrics file starts lying about what it holds.

## Error is weighted by distance, not by sample

`mean_cte` is the integral of cross-track error over arc length divided
by `path_length_m`, and `off_track_dist_m` is the metres driven beyond
`off_track_threshold`. Neither depends on how often odom fired.

Averaging per sample instead makes the number a property of the speed
profile. Odom arrives at a fixed rate, so a slow corner yields far more
samples per metre than a fast straight, and the mean drifts toward
wherever the car was slowest. It also overstates excursions badly,
because the car is usually slowest exactly where it is off line.

The size of that effect on this stack, same run, same seed:

| Measure | Reading |
| --- | --- |
| Off-track samples | 291 of 772, so 38% |
| Off-track distance | 1.66 m of 83.74 m, so 2.0% |

The car is off the racing line for a moment, oscillating slowly, not for
a third of the lap. `off_track_count` is kept in the row because it is
free, but `off_track_dist_m` is the one to read.

`path_length_m` is also a cheap cross-check on the arc length itself:
divided by `duration_s` it must agree with `mean_speed`.

The accumulation lives in
[`track_error.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/src/lhr_metrics/lhr_metrics/track_error.py),
deliberately free of ROS imports so it is unit tested directly. The test
that matters drives one path at two sampling densities and asserts the
weighted mean does not move, where the per-sample mean reports 1.0
against 1.79.

## Clock

Timing comes from the odom message's own `header.stamp`, not from the wall
clock. Wall time made a run's duration depend on host load, so two
otherwise identical runs never produced the same row, which made every
number here useless for comparison.

Reading the stamp works under both launch files without any `use_sim_time`
plumbing, because `lhr_sim_kinematic` and the Gazebo bridge both stamp
odom. An unstamped message warns once and falls back to the node clock
rather than silently reporting a zero duration.

## Parameters

Beyond `RUN_PARAMS`, which only ever reach the CSV:

| Parameter | Default | What it does |
| --- | --- | --- |
| `off_track_threshold` | 2.0 | CTE above this counts as off track |
| `start_radius` | 2.0 | Lap detection: radius of the start zone |
| `start_hysteresis` | 1.0 | How far out counts as having left the start |
| `min_lap_time` | 5.0 | Reject a lap shorter than this |
| `output_csv` | `data/metrics.csv` | Where the row goes |
| `run_id` | wall clock | Row identifier |

## How a run ends

Every ending goes through `finish()`, so a row is always written. Before
this, only Ctrl+C wrote one, and a headless run produced nothing while
still exiting zero.

| `outcome` | Trigger | Exit code |
| --- | --- | --- |
| `lap` | A lap completed | 0 |
| `mission_finished` | Mission state reached `FINISHED` | 0 |
| `emergency` | Mission state reached `EMERGENCY` | 1 |
| `timeout` | `timeout_sec` watchdog fired | 1 |
| `interrupted` | Ctrl+C | 1 |

`timeout_sec` is deliberately **wall** time, not sim time: its job is to
stop a headless run hanging when the simulator dies and sim time freezes.

`mission_finished` is what lets missions with no lap record a row, which
the 75 m acceleration run needs.

In `mvs_demo.launch.py` the metrics process exiting emits a launch
shutdown, so the run ends on its own.

The exit code does **not** survive `ros2 launch`, though:
`LaunchService` returns non-zero only when launch itself raises, never
because a managed node failed. `scripts/run_headless.sh` exists for that
reason, reading `outcome` from the row and exiting on it. A gate must go
through the script.

## Known gaps

- **No assertions.** The row records what happened; nothing yet decides
  whether it passed. That arrives with scenario files.
- **Repeat runs are close, not identical.** Each node is its own process
  with its own executor, so callback interleaving is the OS scheduler's
  call. Gate on a tolerance band, never on equality.
  `scripts/check_stability.sh` measures the band and enforces it.
- **`max_cte` cannot be held to a tight band.** It is an extreme-value
  statistic, so its observed spread only grows as runs are added: 8 runs
  at one seed hold `mean_cte` to 1.03% and `path_length_m` to 0.14%,
  while `max_cte` moves 2.46%. The bands in `check_stability.sh` differ
  per metric for that reason. Whether `max_cte` is *acceptable* is an
  absolute ceiling and belongs in the per-run gate instead.
- **Seed choice still dominates `max_cte`.** Across seeds 1 and 3 the
  weighted `mean_cte` agrees to 2.9% (0.604 against 0.622), but `max_cte`
  differs by 53% (1.88 against 2.89). A single-seed gate on `max_cte`
  measures the seed.

## Perception provenance

Rows record `start_on_track` and `perception` (`sim` or `lidar`), whether the cloud publisher is
actually enabled (`lidar`), `mount_pitch_rad`, and `elevation_profile`.
The kinematic launch passes these values so detector-driven runs can be
separated from the simplified baseline. Existing CSV files with an older
header use the existing schema-mismatch handling; do not compare modes using
only the outcome columns.

The `plant` field distinguishes `kinematic` and `bobsim` runs. BobSim's
commit and dynamics vehicle hash are in the recorded `/lhr/sim/provenance`
message; the existing `vehicle_sha256` column still hashes the autonomy YAML.

`motion_distortion` and `clutter_profile` record the selected LiDAR scene
conditions. The separate [driving study runner](../lhr_demo/README.md#repeatable-driving-studies)
adds truth-based circuit progress, halt observations and map consistency.
