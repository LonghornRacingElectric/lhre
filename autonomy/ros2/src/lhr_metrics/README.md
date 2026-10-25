# lhr_metrics

Measures a run: cross-track error against fixed scene geometry,
distance spent off track, speed stats, and lap completion. Writes one CSV
row per run to `data/metrics.csv` (never committed, see
`autonomy/AGENTS.md`).

## What a row holds

Rows include configuration, fixed-reference identity, and outcomes so two
runs can be compared without guessing which geometry or gate was used.

| Group | Columns |
| --- | --- |
| Identity | `run_id` |
| Provenance | `vehicle_sha256`, `git_sha`, `scenario`, `plant` |
| Scenario | `seed`, `track_style`, `num_waypoints`, `mission`, `perception`, `start_on_track`, `start_finish_cones` |
| Sensor | `lidar`, `mount_pitch_rad`, `elevation_profile`, `return_profile`, `stack_window_sec`, `min_cluster_points`, `ground_z_min`, `fov_deg`, `max_range_m`, `noise_std_m`, `false_negative_rate`, `motion_distortion`, `clutter_profile` |
| Estimator | `estimator`, `gyro_bias`, `wheel_scale_error`, `lidar_classify_sides`, `mean_pos_error`, `max_pos_error`, `mean_yaw_error` |
| Control | `lookahead_dist`, `a_lat_max`, `v_min`, `v_max`, `max_accel`, `max_decel` |
| Reference | `reference_sha256`, `reference_source`, `reference_length_m`, `reference_world`, `reference_closed` |
| Scoring | `course_sha256`, `footprint_model`, `sweep_step_m`, `off_track_threshold`, `start_radius`, `min_lap_time`, `min_progress_fraction`, `lap_start_speed`, `max_odom_gap_sec` |
| Circuit | `lap_progress_m`, `lap_time_s`, `lap_invalid_reason`, `scoring_odom_topic`, `lap_start_stamp_s`, `lap_gate_x_m`, `lap_gate_y_m`, `lap_gate_tangent_x`, `lap_gate_tangent_y` |
| Contacts | `cones_hit`, `cone_hit_ids` |
| Outcomes | `outcome`, `duration_s`, `samples`, `path_length_m`, `mean_cte`, `max_cte`, `off_track_count`, `off_track_dist_m`, `mean_speed`, `max_speed`, `lap_completed` |

`git_sha` comes from the launch file and carries a `-dirty` suffix when the
tree has uncommitted changes, since a clean-looking id on a modified tree
is worse than no id. Only `mvs_demo.launch.py` fills `git_sha` and the
Sensor and Control columns. `gazebo_demo.launch.py` passes neither, so
Gazebo rows read `unknown` and the node's MVS defaults there (`lidar`
false, `min_cluster_points` 3 while Gazebo runs 1); take a Gazebo run's
commit and settings from its launch arguments. `vehicle_sha256` hashes `lhr_vehicle`'s config file
with the same formula the study harness in `simulation/tools` uses, so a
sim run and a BobDyn study are comparable on that field.

The independent variables are declared in `RUN_PARAMS` and passed in by
the launch file. **A new launch argument that changes a result belongs in
that tuple too**, or rows quietly stop explaining themselves. Scoring
settings live in `SCORING_PARAMS` and are also written on every row.

A `data/metrics.csv` written under an older column set is moved aside to
`data/metrics.csv.old` with a warning rather than having its extra fields
dropped, which is how a metrics file starts lying about what it holds.

## Reference and lap contract

The node never subscribes to the planner's `/lhr/track/centerline`.
MVS latches one complete `/lhr/track/cones` truth message (reliable,
transient local) and pairs its ordered `left_cones` and `right_cones` to
make the reference polyline. These scene markers must describe the whole
course, not the observed cone map. Changes after the first valid reference
are ignored. Gazebo passes the actual selected SDF as `reference_world`;
indexed `cone_blue_N` / `cone_yellow_N` includes provide the same ordered
geometry, including the file's rounded coordinates. Matching indices may
have gaps; missing partners still fail. For the generated straight acceleration
layout, all blue/yellow/orange boundary cones are paired by x position and
side of y=0, retaining the orange start/finish and stopping lane. It does not regenerate
a potentially different seed or use the perception map.

With `truth_odom_topic` configured, only its truth samples can update CTE,
progress, off-course checks or lap acceptance. `/lhr/vehicle/odom` is then
used only to measure estimator error. Missing truth never falls back to the
estimate. Without a separate truth topic, the plant-owned vehicle odometry
is scored directly. The CSV records `scoring_odom_topic`.

CTE is distance to the reference's **segments**, including the closing
segment only when `reference_closed=true`. There is no midpoint-spacing
floor. `reference_sha256` hashes the ordered geometry and closure flag;
old planner-relative rows and new reference-relative rows are not comparable.

The first valid odometry position fixes a gate on the reference, with the
local tangent defining the forward crossing direction. The gate is a line
segment with half-width `start_radius`, not a moving planner point or a
start-zone return. At a vertex its normal bisects the adjacent tangents.
Its coordinates, tangent and driving-onset stamp are recorded in the CSV so
late initial observations cannot silently change a comparison. This is a
run-local gate at the observed start, not the optional orange
scene gate or an event-rule timing claim.

Timing starts at the first stamped sample with speed at least `lap_start_speed`
while the mission reports `DRIVING`. Without a mission-status publisher,
speed alone starts scoring. Stationary READY time and duplicate simulation
timestamps earn no duration or progress. A lap requires signed forward
progress of at least `min_progress_fraction` of the reference length, a
forward gate crossing within its width, and `min_lap_time`. Crossing time
is interpolated between the two odometry stamps and written as `lap_time_s`.
`duration_s` is time from driving onset to the last scored sample.

Missing reference truth or missing stamps produce no scoring samples and
cannot pass. Late initial observations receive no credit for unseen travel.
An odometry gap, a backward clock, an off-course chord between observations,
or a discontinuous jump invalidates lap acceptance for the rest of that run.
The node terminates with `outcome=invalid_lap` and exit code 1; the final
row records the last scoring sample and `lap_invalid_reason`. Start a new
run to reset it. Progress continuity checks the base-link chord against the
scene boundary strips. Whole-vehicle checks additionally cover translation
and shortest-angle yaw interpolation between continuous observations; see below.
Neither assumes an unobserved nonlinear trajectory between real sensor samples.
Closed references must enclose nonzero area; collinear or degenerate loops
are rejected. This scorer does not judge a multi-loop skidpad route. An open
reference supports CTE/progress but never publishes a circuit lap.

The accepted flag is published once on `/lhr/metrics/lap_complete`, which
the mission manager latches. Autocross `FINISHED` without a verified lap is
recorded as `invalid_completion` and fails. The mission manager then
publishes `/lhr/mission/complete` and pursuit brakes to a stop before
FINISHED; this node never feeds reference geometry to control.

## Footprint, boundaries and contacts

`course.py` latches the same scene truth as the centerline, including orange
start/finish cones. The road is the union of quadrilateral strips between
consecutive left/right pairs. Closed courses retain their inner island;
open courses include start/end caps. This supports the ordered generated
courses, not arbitrary SDF meshes or crossed/branching boundary layouts.

A conservative rectangle encloses the YAML chassis box and all steering
orientations of the YAML wheel envelopes. Its origin follows `base_link`
(the rear axle); its nose is not centered on the odometry position. Checking
all corners plus boundary-segment intersections catches a side, nose or
wheel extending outside even when the rear axle has low CTE. Space between
collision solids is included, so this can reject a geometric near miss that
separate 3D solids would allow. The YAML contains assumed dimensions; this
is not a measured physical car envelope.

For continuous observations, the sweep interpolates XY and shortest-angle
yaw. Translation plus maximum corner travel per step is bounded by 0.05 m.
Each sample rectangle is padded by half that step's motion bound (at most
0.025 m), conservatively covering motion between samples. This catches a
contact or excursion with safe endpoints, including rotation in place.
Telemetry gaps, backward time and discontinuous progress fail the run instead
of interpolating across unobserved travel. Duplicate timestamps are ignored.

Cone contacts use each nominal cone's oriented square base from
[lhr_trackgen](../lhr_trackgen/README.md), including both orange sizes.
`cones_hit` counts distinct scene identities touched by the swept envelope;
`cone_hit_ids` records them. Repeated overlap counts once. A footprint
excursion (`vehicle_off_course`) or contact (`cone_contact`) terminates the
run as `invalid_lap`; neither can publish a successful circuit flag. All
contacts in the final checked interval are counted before stopping.
These are conservative geometric contacts with static nominal cones, not
Gazebo force-sensor events or a count of cones physically knocked down.

`course_sha256` hashes boundaries, nominal cone poses/kinds/base widths,
closure, the vehicle envelope and sweep resolution. `footprint_model` and
`sweep_step_m` record the scoring convention. Existing `reference_sha256`
continues to identify only the CTE centerline. Historical off-track/contact
rows must be rebaselined against this convention.

`off_track_count` counts scored samples/intervals with an envelope excursion.
`off_track_dist_m` conservatively includes the entire odometry chord for each
violating interval, ending at the first failure; it is not an exact crossing
length and retains sampling uncertainty. It replaces the old CTE-threshold
classification. `off_track_threshold` remains a legacy progress-continuity
scale (chord probe spacing and arc-jump tolerance), not the road width.

## Error is weighted by distance, not by sample

`mean_cte` remains the trapezoidal integral of segment CTE over distance
divided by `path_length_m`. Changing speed or sample density should not
reweight slow corners. `max_cte` is the largest scored segment distance.
CTE remains a rear-axle tracking metric, separate from whole-vehicle validity.
The pure `track_error.py` tests check the distance weighting at different
sampling densities; footprint/contact tests separately check course validity.

## Clock

Timing comes from the odom message's own `header.stamp`, not from the wall
clock. Wall time made a run's duration depend on host load, so two
otherwise identical runs never produced the same row, which made every
number here useless for comparison.

Reading the stamp works under both launch files without any `use_sim_time`
plumbing, because `lhr_sim_kinematic` and the Gazebo bridge both stamp
odom. An unstamped message warns once and is dropped; there is no wall-clock
fallback that could turn a simulation pause into lap time.

## Parameters

Beyond `RUN_PARAMS`, which only ever reach the CSV:

| Parameter | Default | What it does |
| --- | --- | --- |
| `off_track_threshold` | 2.0 | Legacy progress continuity scale, metres; road width comes from truth boundaries |
| `start_radius` | 2.0 | Half-width of the fixed crossing gate, metres |
| `reference_world` | empty | Generated SDF truth source; empty waits for scene markers |
| `reference_closed` | true | Include closure and circuit laps; launch defaults false for acceleration |
| `min_progress_fraction` | 0.98 | Minimum signed course progress before a gate crossing |
| `lap_start_speed` | 0.1 | Minimum speed to start the simulation-time timer, m/s |
| `max_odom_gap_sec` | 1.0 | Largest gap allowed during a scored circuit |
| `min_lap_time` | 5.0 | Reject a lap shorter than this |
| `timeout_sec` | 0.0 | Optional wall-clock watchdog; zero disables it |
| `truth_odom_topic` | empty | Truth odometry to score; the estimate on `/lhr/vehicle/odom` then only feeds the error columns |
| `output_csv` | `data/metrics.csv` | Where the row goes |
| `run_id` | wall clock | Row identifier |

`start_hysteresis` is removed: gate direction and circuit progress replace
start-zone arming. Both launchers expose the scoring arguments with explicit
ROS types. Gazebo resolves an empty `reference_world` argument to its selected
world; MVS keeps the node's empty default and waits for scene truth. Both
launchers default `reference_closed=false` when mission or track style is
`acceleration`; an explicit override is still honored.

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
| `invalid_completion` | Autocross finished without a verified circuit | 1 |
| `invalid_lap` | Scoring continuity or course check invalidated the run | 1 |

`timeout_sec` is deliberately **wall** time, not sim time: its job is to
stop a headless run hanging when the simulator dies and sim time freezes.
It is disabled by default so a slow Gazebo run can finish.
`scripts/run_headless.sh` explicitly enables a 120 s deadline; a later
`timeout_sec:=...` launch argument overrides that deadline. The driving study
runner disables metrics and enforces its own `--wall-timeout`.
`scripts/check_stability.sh` also sets a 120 s deadline; pass
`timeout_sec:=...` to either script when using a slower plant.

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

- **A lap is not a full acceptance verdict.** The node validates circuit
  completion and telemetry continuity; hardware fidelity, actuator limits and
  event-specific gates need their own criteria.
- **Repeat runs are close, not identical.** Each node is its own process
  with its own executor, so callback interleaving is the OS scheduler's
  call. Gate on a tolerance band, never on equality.
  `scripts/check_stability.sh` measures the band and enforces it.
- **Historical stability bands need a new baseline.** Old numbers below
  were measured against planner points, not fixed reference segments.
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

## Estimator comparisons

`truth_odom_topic` selects truth for all course scoring and enables the
position and yaw error columns for the separate estimate.
They remain blank without a truth stream. The CSV includes `estimator`,
`gyro_bias`, `wheel_scale_error`, and `lidar_classify_sides` alongside the
existing run parameters. Distance-weighted CTE and run outcome checks remain
active. Error accumulation starts only after driving/scoring onset and excludes
stationary READY observations. Each estimate is compared with truth
interpolated at the estimate's own stamp (`alignment.py`): an estimate waits
until truth at or past its stamp arrives, and one older than the 2 s truth
buffer is skipped. Comparing with the latest received truth sample instead
scored scheduling, since a 0.1 s stall at 5 m/s reads as 0.5 m of error
([#165](https://github.com/LonghornRacingElectric/lhre/issues/165)).

Node cleanup tolerates an already-stopped ROS context after launch shutdown.
