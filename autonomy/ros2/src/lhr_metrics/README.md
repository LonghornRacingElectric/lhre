# lhr_metrics

Measures a run: cross-track error against the published centerline,
off-track samples, speed stats, and lap completion. Writes one CSV row per
run to `data/metrics.csv` (never committed, see `autonomy/AGENTS.md`).

## What a row holds

26 columns in four groups, because a row of results alone cannot be
compared with another row: nothing in it says what differed between the
two runs that produced them.

| Group | Columns |
| --- | --- |
| Identity | `run_id` |
| Provenance | `vehicle_sha256`, `git_sha`, `scenario` |
| Scenario | `seed`, `track_style`, `num_waypoints`, `mission` |
| Sensor | `fov_deg`, `max_range_m`, `noise_std_m`, `false_negative_rate` |
| Control | `lookahead_dist`, `a_lat_max`, `v_min`, `v_max`, `max_accel`, `max_decel` |
| Outcomes | `duration_s`, `samples`, `mean_cte`, `max_cte`, `off_track_count`, `mean_speed`, `max_speed`, `lap_completed` |

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
- **Repeat runs are close, not identical.** One seed repeated lands within
  about 0.5% on averaged metrics and exactly on `max_cte`. Each node is its
  own process with its own executor, so callback interleaving is still the
  OS scheduler's call. Gate on a tolerance band rather than equality.
- **`mean_cte` is sample-weighted**, so slow sections count more than fast
  ones. Weighting by distance travelled would be both more meaningful and
  more stable.
