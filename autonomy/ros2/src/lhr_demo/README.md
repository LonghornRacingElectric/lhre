# lhr_demo

Launch files for the kinematic stack. `mvs_demo.launch.py` is the one the
gate and the stability check both run, so it is where a run's independent
variables are declared and where its provenance is stamped.

## Why the launch file owns provenance

`git_sha` is resolved here, not in a node, and from
`Path(__file__).resolve().parent` rather than the working directory. A run
started from anywhere but the checkout would otherwise record `unknown`
or, worse, an unrelated repository's commit. `build.sh` passes
`--symlink-install`, so the installed copy resolves back into the
checkout. A `-dirty` suffix marks uncommitted changes, because a
clean-looking id on a modified tree is worse than no id at all.

Every argument that changes an outcome is also passed to `lhr_metrics` for
the CSV row. **A new argument that affects a result belongs in both
places**, or rows quietly stop explaining themselves.

## Arguments

Beyond the node parameters listed in
[ros2/README.md](../../README.md#parameters):

| Argument | Default | What it does |
| --- | --- | --- |
| `use_sim_time` | `true` | Every node but `sim_kinematic` follows `/clock`. Off means wall time, and averaged metrics wander |
| `timeout_sec` | `120.0` | Wall-clock watchdog, so a dead sim ends the run instead of hanging |
| `output_csv` | `data/metrics.csv` | Where the metrics row goes |
| `run_id` | timestamp | Shared by the metrics row and the bag directory |
| `record` | `false` | Record an MCAP bag of the contract topics |
| `bag_dir` | `data/bags` | Parent directory for bags |
| `foxglove` | `false` | Serve the live graph over websocket |
| `foxglove_port` | `8765` | Bridge port |
| `vehicle_mesh` | `true` | Draw the CAD car; `false` draws primitives a bag can render |
| `scenario` | `mvs_demo` | Name recorded on the row |

`use_sim_time` reaches `sim_kinematic` as `publish_clock` instead, because
a clock source that waits on its own clock never ticks.

## Recording

```bash
ros2 launch lhr_demo mvs_demo.launch.py record:=true
ros2 bag info data/bags/<run_id>
```

MCAP is the default storage in Jazzy, so recording needs nothing extra
installed. The bag lands in `data/bags/<run_id>` using the same `run_id`
the metrics row carries, which is the whole point: a row reporting a bad
number names the recording that explains it.

`RECORDED_TOPICS` in the launch file is an explicit list, not `--all`.
The viz topics republish every cone marker at rate and would dominate the
bag, and the list doubles as a statement of what the seam between lanes
is. `/clock` is in it because a sim-time bag without it has no timeline a
viewer can read. `/lhr/lidar/points` stays in even though the kinematic
stack never publishes it, so the same recorder works unchanged under
Gazebo and on the car; a topic nothing publishes costs nothing.

Expect roughly 6 MiB per 17 s lap, dominated by the cone `MarkerArray`
topics. That is why `record` is off by default: the gate runs many seeds.

The recorder is spelled twice, once per clock mode, because
`--use-sim-time` is a bare flag that no substitution can switch, and
passing it when nothing publishes `/clock` leaves the recorder waiting on
a clock that never ticks.

Each bag also carries its whole argument list as rosbag2 `custom_data`,
readable in `metadata.yaml`, so a bag found on disk months later says
what produced it:

```yaml
custom_data:
  git_sha: 4eb6318
  run_id: provcheck
  mount_pitch_rad: 0.25
  v_max: 10
  seed: 3
  ...
```

The arguments are declared and recorded off one list, `launch_args`,
filtered by `UNRECORDED_ARGS`. That is deliberate. An earlier version
named four keys by hand, and a bag recorded with a 14.3 degree Mid-360
mount pitch was indistinguishable from one at the default: the pitch
changes every point in the cloud, and recovering it meant solving the
geometry backwards from the points. Deriving the list means a newly
added argument is recorded without anyone remembering to add it.

`UNRECORDED_ARGS` holds the ones that cannot change what the car did:
where output lands (`output_csv`, `bag_dir`), what is watching
(`foxglove`, `foxglove_port`, `enable_metrics`, `record`), and `run_id`,
which is written ahead of the rest. **An argument belongs there only if
it cannot change a result.** When in doubt, record it.

### Parameter types, and why they are declared

Launch hands parameters to a node through a YAML file, so a value
arrives as whatever YAML decides it is rather than what the node
expects. Two ways that bit:

- `v_max:=10` becomes the integer 10, a node declaring a double rejects
  it, and the run dies on an `InvalidParameterTypeException` for want of
  a decimal point. Every float argument had this: `timeout_sec:=60`,
  `lookahead_dist:=4`, `mount_pitch_rad:=0`.
- `run_id:=20261004_120000` becomes the integer `20261004120000`,
  because YAML treats underscores as digit separators. That silently
  renamed the run and left the bag directory and the metrics row
  disagreeing, defeating the correlation above.

Every parameter taken from a launch argument is therefore wrapped in
`ParameterValue(..., value_type=...)` through the `_f`, `_i`, `_s` and
`_b` helpers, which makes launch do the conversion. Round numbers work,
and a numeric-looking string stays a string. **A new parameter added to
a node needs the matching helper**, or it reintroduces this.

`run_id` still defaults to `%Y%m%dT%H%M%S` with a `T` rather than an
underscore, which is now belt as well as braces. The metrics node also
accepts a numerically-typed `run_id` and coerces it, so running that
node directly, outside this launch file, cannot crash over a cosmetic
field either.

## Viewing

```bash
ros2 launch lhr_demo mvs_demo.launch.py foxglove:=true   # live
```

Then open Foxglove and connect to `ws://localhost:8765`. Needs
`ros-jazzy-foxglove-bridge`, which `package.xml` declares, so a normal
`rosdep install` provides it; the macOS Docker image names it explicitly
because nothing in that image runs `rosdep`.

`ws://localhost:8765` is also the right address from the macOS image,
because `compose.yaml` publishes the port. Reaching the container's own
IP instead happens to work under OrbStack and does not under Colima,
which is why the port is published rather than documented around.

For a recorded run, open the bag directly in Foxglove instead; no bridge
and no ROS install involved, which is also what makes MCAP the right
format for the sensor work that happens outside ROS.

**Live and recorded are for different jobs, and mixing them up is the
usual reason this feels heavy.** Designing something, like deciding
where the lidar goes, wants the live bridge: the parameter panel moves
the mount and the next frame shows the result, with no bag written at
all. Bags are for the other job, comparing one run against another,
which is the only reason the gate needs them. `record` is off by
default for exactly this reason.

### Running until you stop it

A normal run ends itself, which is the right thing for a gate and wrong
for sitting in front of a viewer: a completed lap calls `finish('lap')`
in `lhr_metrics`, the process exits, and its exit emits `Shutdown`. About
17 s in, the viewer's connection drops.

`enable_metrics:=false` removes that. Nothing then publishes
`/lhr/metrics/lap_complete`, so `mission_manager` never leaves `DRIVING`,
the car keeps lapping, and no process exit tears the launch down. It runs
until Ctrl-C. `timeout_sec:=0.0` disables only the wall-clock deadline
and leaves lap completion in place, so it is not enough on its own.

```bash
ros2 launch lhr_demo mvs_demo.launch.py foxglove:=true enable_metrics:=false
```

The cost is that no metrics row is written, so an indefinite run tells
you nothing comparable. That is the trade: watch the car, or score it.

Either way, load the shared layout from
[`foxglove/lhr_sim.json`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/foxglove/lhr_sim.json)
so everyone is looking at the same panels. A layout kept only in a
personal Foxglove install means two people debugging the same bag see
different things.

Its structure is checked against a real Foxglove export, and
`./scripts/check_layout.sh <bag>` verifies that every topic and frame it
names exists in a recording. How the app *renders* it is still
unconfirmed, because that cannot be tested from here. If a panel is
empty after `check_layout.sh` passes, fix the layout in the app and
re-export over the file.

Note that `lidar:=true` is what publishes the `base_link -> lidar`
transform. Without it a recorded cloud has no frame to sit in. The car
itself comes from `vehicle_viz`, which always runs, whether or not the
lidar was on.

What the car looks like depends on `vehicle_mesh`, and it matters more
for a bag than for a live run. The default, `true`, draws Orion's CAD as
a `MESH_RESOURCE` marker, which names an asset the viewer **fetches**:
4.2 MB once per session, which a live `foxglove_bridge` serves and a bag
cannot, because a bag has no asset server. So a recorded run opens with
the car missing. Record with
`vehicle_mesh:=false` when the bag itself has to show a car, and you get
the box and four cylinders instead. The argument is in the bag's
provenance either way, so an empty-looking recording can be explained
from the bag rather than from memory.

## Ending a run

The metrics process exiting emits a launch `Shutdown`, so the whole graph
comes down on its own and a headless run cannot hang. The exit code does
not survive `ros2 launch`, though, since `LaunchService` returns non-zero
only when launch itself raises. Gates go through
[`scripts/run_headless.sh`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/scripts/run_headless.sh).

## LiDAR perception

`perception:=sim` keeps the simplified cone detector and indexed complete
track. `lidar:=true` alone previews the cloud without changing perception.
`perception:=lidar` enables the Mid-360 cloud simulator, disables simplified
detections and selects the cloud detector, geometric boundary planning and
open-path control. That mode defaults to `v_max:=4.0` rather than 12 m/s.

`start_on_track:=true` aligns the LiDAR-mode initial pose with the nearest
generated centerline and its forward tangent; `false` preserves explicit
`init_x`, `init_y`, `init_yaw`. Scene geometry sets only the starting pose;
cone IDs and ground-truth centerlines do not reach the LiDAR planner.
When no connected forward route exists, the controller stops. Full-lap
reliability on complex autocross tracks remains open work.

## Cone and accumulation studies

Scene cones now use striped meshes and square feet rather than sphere
markers. Optional `start_finish_cones:=true` adds nominal large orange gate
cones; see [track generation](../lhr_trackgen/README.md) for layout limits.

LiDAR detection defaults to `stack_window_sec:=0.5`,
`min_cluster_points:=3` and `ground_z_min:=0.05`. Set the window to zero for
single-scan comparisons. These launch arguments reach the detector and are
recorded in metrics and bag metadata. They are candidate settings informed
by the acceptance report, not hardware-validated tuning. See
[perception](../lhr_perception/README.md) and
[acceptance comparison](../lhr_lidar_sim/README.md#acceptance-comparison).

`return_profile:=acceptance_overcast` selects the fitted stationary
small-cone return-density profile. `baseline` is the default. The selection
is recorded in CSV and bag metadata; see
[the measured fit and limits](../lhr_lidar_sim/README.md#recorded-overcast-return-profile).

## BobSim vehicle dynamics

Select `plant:=bobsim` to replace the kinematic vehicle with
[BobSim 3 DOF](../lhr_sim_bobsim/README.md). The default is `plant:=kinematic`.
Both use the same perception, planning and command topics. In BobSim mode,
`max_accel` and `max_decel` also limit the plant's speed-to-torque controller.
Bags record `/lhr/sim/provenance`; metrics record the selected `plant`.

## Repeatable driving studies

Build the workspace, source it, and run in the ROS environment:

```bash
ros2 run lhr_demo driving_study --output data/studies/run-001 \
  --seeds 1 7 --speeds 4 6 --plants kinematic bobsim \
  --conditions clean motion clutter outdoor
```

Cases run sequentially on isolated ROS domain 66. Override `--domain-id`
if needed. Each case stops after one forward circuit, 90 simulated seconds,
or a 240 s wall watchdog. Both limits are configurable. The runner stops
its own process group on completion or interruption. Existing output
directories are rejected so studies do not overwrite each other.

Each case saves arguments, outcomes, dynamics provenance, a launch log and
a `trajectory.csv` with position, measured/requested speed and error.
The matrix saves `manifest.json`, `summary.json` and `summary.csv` after each
case, so a failed case remains reviewable. The manifest records repository
revision, dirty status and per-file runtime source/configuration hashes; commit reviewed changes before formal studies.
The runner exits nonzero if any case misses a circuit or travels off track.
For a short diagnostic use `--sim-seconds 12`; incomplete circuits then
remain `sim_limit` results, not successful laps.

`clean` uses frozen scans and no clutter. `motion` uses rolling scans.
`clutter` adds trackside objects to rolling scans. `outdoor` also enables
the recording-informed overcast dropout profile. See
[LiDAR](../lhr_lidar_sim/README.md#motion-and-clutter-studies) for assumptions.

Scores use ground truth only in the observer: centerline error, distance
outside the cone-center corridor, forward circuit progress, observed halts,
and simulated seconds per total wall second (including startup). A halt
means speed below 0.2 m/s after the car has first moved and the first three
simulated seconds; events
must last at least one second. These are observed stops, not proof of an
incorrect planner decision. Corridor error scores the rear axle, not the
whole vehicle footprint. Mapped candidate matches use a 0.35 m radius;
repeated persistent-map publications count repeatedly. This is a map
consistency measure, not per-scan recall or statistically independent
precision. Truth never enters LiDAR detection or path planning.
