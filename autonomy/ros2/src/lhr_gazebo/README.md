# lhr_gazebo

Gazebo Harmonic vehicle physics, sensor bridging, and demo launch. Run
`ros2 launch lhr_gazebo gazebo_demo.launch.py` in a supported ROS environment.
`perception:=sim` uses simplified cone detections; `perception:=lidar` uses
[lhr_perception](../lhr_perception/README.md) and boundary planning.
Both perception modes select open-path control: the track builder publishes
a forward prefix, so lookahead and curvature must not connect its ends.
See [the workspace reference](../../README.md) for launch arguments and
vehicle physics.

## LiDAR coordinates

Gazebo preserves main's level LiDAR mount at z=0.55 m and its generated
cloud frame. The detector reads that mount from vehicle YAML rather than
requiring a new static TF. Its height band stays in the sensor frame.
The Mid-360 path uses scan-time TF and base-frame heights instead; do not
copy its detector thresholds into Gazebo without running a Gazebo gate.

These LiDAR-only arguments preserve Gazebo's existing values and are typed
before node startup. They have no effect in `perception:=sim`.

| Argument | Default |
| --- | --- |
| `lidar_ground_z_min` | `-0.40` |
| `lidar_ground_z_max` | `0.5` |
| `lidar_min_cluster_points` | `1` |
| `lidar_dedup_radius` | `1.5` |
| `lidar_max_cluster_points` | `50` |
| `lidar_stack_window_sec` | `0.0` |
| `lidar_stack_max_frames` | `10` |
| `lidar_transform_wait_sec` | `0.5` |
| `lidar_sensor_transform_source` | `vehicle_config` |
| `lidar_height_filter_frame` | `sensor` |

For example, `perception:=lidar lidar_min_cluster_points:=2` overrides the
cluster threshold. A pitched mount requires `lidar_sensor_transform_source:=tf`
and a published scan-time mount transform; select `lidar_height_filter_frame:=base_link`
when using ground-relative thresholds. These opt-in configurations need
Gazebo acceptance testing before changing defaults.

Gazebo still uses its existing uniform-grid VLP-16 model. The imported Livox
pattern belongs to the kinematic [Mid-360 simulator](../lhr_lidar_sim/README.md).

### Range noise

The GPU LiDAR applies independent, zero-mean Gaussian noise along each beam
before publishing the point cloud. Its standard deviation comes from
`sensors.lidar.range_noise_std_m` in `lhr_vehicle/config/vehicle.yaml`:
`0.02` m (2 cm) by default. This rounds the upper end of the 10/4 acceptance
test's 0.9-1.9 cm floor-patch residuals and matches the existing
[Mid-360 simulator](../lhr_lidar_sim/README.md)'s range-noise setting.
Floor-plane residuals are not a direct along-beam calibration; this is a
provisional noise scale, while Gazebo retains its VLP-16 sampling geometry.
See the [acceptance log](../../../testing/2026-10-04-mid360-acceptance/log.md#outdoors-driveway-in-front-of-the-garage-from-1637).

The setting must be finite and non-negative. Set it to `0.0` for an ideal-range
comparison, then regenerate the model and rebuild as below. It is model
configuration, not a ROS node parameter or launch argument. Sensor range
limits still apply; angular error, dropout and reflectivity are separate models.

#### Validation (2026-10-23)

Checked against main `1f694cc` in the ROS 2 Jazzy / Gazebo Harmonic container
on Apple Silicon with software OpenGL. In the stationary target fixture,
100 repeated central-beam ranges at 5 m had measured standard deviation
0.01966 m with the 0.02 m setting and zero with noise disabled.

Both seed-1 runs used `perception:=lidar estimator:=truth gui:=false
rviz:=false enable_stereo_camera:=false`, default control parameters, and
the generated 0.02 m noise model. Production metrics scored:

| Track | Outcome | Lap time | Maximum CTE | Off-track distance | Cone contacts |
| --- | --- | --- | --- | --- | --- |
| Oval | `lap` | 27.7521 s | 0.2049 m | 0 m | 0 |
| Autocross | `lap` | 33.6125 s | 0.2609 m | 0 m | 0 |

A zero-noise autocross comparison also completed a valid lap with zero
off-track distance and cone contacts. These are single-run smoke checks,
not a measured completion probability or validation of the real sensor.

## Generated model

Edit the template or vehicle YAML, then run
`python3 src/lhr_gazebo/scripts/generate_vehicle_model.py` from the ROS workspace.
Commit the regenerated model alongside the source. The model consistency tests
run with `colcon test --packages-select lhr_gazebo` and need no GUI.

## Competition cone assets

The four cone models are generated from
[lhr_trackgen](../lhr_trackgen/README.md)'s shared nominal dimensions and
profile. They include square feet, colored stripe meshes and enabled
collision meshes. Regenerate from the ROS workspace with
`python3 src/lhr_gazebo/scripts/generate_cone_models.py`, then commit the
SDF and all three meshes per cone. Tests compare every generated asset.

Cones are static: a vehicle can collide with them, but they do not tip or
move. The stored masses describe the nominal assets and are not dynamic
physics parameters. Existing world origins remain at half cone height;
the generated mesh offsets place their feet at ground level. The MVS
optional gate layout is not added to existing generated Gazebo worlds.

The seed-1 autocross and oval fixtures use the corrected closed-boundary
spacing from [trackgen](../lhr_trackgen/README.md). Regenerate worlds after
changing track generation so the Gazebo scene matches MVS track truth.

## Run

```bash
cd autonomy/ros2
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar estimator:=truth
```

`perception:=sim` uses labelled generator cones and color-aware Delaunay
(`boundary`) pairing. The default `constrained_greedy` chaining orders its
midpoints from the vehicle using distance and heading limits.
`perception:=lidar` clusters the simulated pointcloud and maps unclassified
cones by default. The track builder uses `boundary` pairing. Use
`estimator:=truth` to check perception without EKF drift, then repeat with
`estimator:=ekf`.

To opt into left/right inference and one-to-one `classified` pairing, run:

```bash
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar lidar_classify_sides:=true estimator:=truth
```

By default, mapped cones appear orange in RViz. The unclassified `boundary`
strategy can produce incorrect centerlines on sharp autocross turns; the
classifier remains opt-in while its sparse-map failures are investigated.

The geometry-based side classifier is experimental. In a live autocross run,
the cone map had no duplicate cones, but sparse early detections caused a
right-side boundary fragment to be labelled left and the car departed the
course. The generated-track regression does not reproduce the LiDAR's
occlusion and sparse returns.

The launch starts graphical Gazebo and RViz by default. Use `gui:=false` or
`rviz:=false` for a headless run. See the workspace
[README](../../README.md) for all launch arguments and topic diagrams.

Every argument the launch forwards to a node carries the node's declared
type, so a whole number like `v_max:=8` still reaches `pure_pursuit` as a
double. Untyped, launch_ros would guess from the text and hand the node an
integer, which kills it at startup. `test_launch_param_types.py` checks every
node the launch can start for a parameter fed by a bare `LaunchConfiguration`.
It does not see values the launch reads from `context.launch_configurations`
itself or wraps in another substitution such as `PythonExpression`; type
those by hand.

## Local path control

`path_timeout_sec` is a typed double launch argument (default `1.0`, matching
pursuit) in both perception modes, including `lidar_classify_sides:=true`.
For example, `path_timeout_sec:=2` changes the local-path expiry deadline.

`closed_path` is fixed to `false` in both Gazebo branches: each publishes a
car-anchored prefix, not a complete circuit. Lookahead and curvature do not
connect the endpoints. Tests check default/override startup, endpoint stopping,
and stale-path stopping; they do not establish Gazebo driving performance.

`planning_horizon_m`, `max_path_gap_m`, and `max_path_turn_deg` only affect
`boundary_graph`, which Gazebo does not select. They are intentionally not
Gazebo launch arguments. The current `boundary` / `classified` modes use
their chaining arguments documented above instead.

## Worlds

`worlds/` holds the generated tracks the launch picks by `track_style` and
`seed`: `autocross_seed1` to `seed5` (the MVP gate's seeds), `oval_seed1`,
and `acceleration_seed1`. A missing world is an error, not a substitute:
any other world would disagree with the cones `publish_cones` generates
for that seed. Generate it from `autonomy/ros2`, then rebuild:

```bash
python3 src/lhr_gazebo/scripts/generate_world.py --style autocross --seed 6 \
    --output src/lhr_gazebo/worlds/autocross_seed6.sdf
```

`./scripts/run_gazebo_demo.sh` passes its arguments straight to the launch,
which picks the world the same way, so the world always matches the cones,
mission and pairing width.

`test_world_cones.py` fails when a committed world no longer holds exactly
the cones trackgen publishes for its style and seed, so regenerate after
changing a generator. The acceleration course is a fixed rules layout with
4.9 m clear between cone bases, staged with the nose 0.30 m behind the
start line. `track_style:=acceleration` always runs the acceleration
mission, whatever `mission:=` says, and `mission:=acceleration` on another
track fails the launch: the course only completes under its own mission.
It pairs cones at the course's 5.13 m center spacing, and the mission
brakes the car in the exit lane once the nose crosses the finish:

```bash
./scripts/run_gazebo_demo.sh track_style:=acceleration perception:=lidar estimator:=ekf
```

## Command timeout

`joint_cmd_adapter` stops the wheels when `/lhr/vehicle/cmd` has been silent
for `adapter_cmd_timeout_s` (0.3 s; `0` disables). Gazebo's joint controllers
would otherwise hold the last target and keep a car with a dead controller
driving.

## Fault injection

`fault:=<kind>` starts `fault_injector`, which breaks one thing
`fault_after_s` (30) after the mission reaches `DRIVING`, for
`fault_duration_s` (0 = until shutdown), and announces it on
`/lhr/sim/fault`. It exists to test `lhr_safety`
([#117](https://github.com/LonghornRacingElectric/lhre/issues/117)); without
`fault:=` nothing is rewired.

| Kind | How |
|---|---|
| `ekf_kill`, `controller_kill` | `pkill -9` the node |
| `controller_pause` | `SIGSTOP` the controller, `SIGCONT` after `fault_duration_s` (with the default 0 it stays frozen) |
| `odom_nan`, `odom_spike` | The odometry producer (the EKF, or the bridge under `estimator:=truth`) publishes `/lhr/vehicle/odom_raw`; the injector relays it, corrupted |
| `lidar_drop` | The bridge publishes `/lhr/lidar/points_raw`; the injector stops relaying |
| `cmd_nan` | `pure_pursuit` publishes `/lhr/vehicle/cmd_raw`; the injector relays it with NaN steering |
| `steer_freeze` | `joint_cmd_adapter` reads `/lhr/vehicle/cmd_actuated`, which the injector holds at the fault's steering angle |

`ekf_kill` needs `estimator:=ekf` (there is no `ekf_node` otherwise) and
`lidar_drop` needs `perception:=lidar` (`safety_node` only watches the LiDAR
there); the launch refuses either combination rather than run a fault that
cannot trip. The `/lhr/sim/fault` event goes out before the signal, and the
injector logs an error if `pkill` finds no process.

The matrix runner and scoring are in
[`autonomy/testing/tools/fault_injection`](../../../testing/tools/fault_injection/README.md).

## Safety monitor

`safety:=true` adds `lhr_safety`'s `safety_node`, which trips the mission
on stale odometry, commands or LiDAR, a diverged EKF, leaving the geofence,
or steering that does not track. It is off by default while the thresholds
are tuned; each one is a `safety_*` launch argument. See
[lhr_safety](../lhr_safety/README.md).

With safety on, the mission manager also waits for the safety heartbeat to
be steady for `safety_settle_s` (2.0) before READY, so safety only arms
once every stream it watches is flowing. That follows `safety:=` rather
than being its own argument: without `safety_node` there is no heartbeat.

## Visual check

For a stationary preview of the skidpad circular boundaries (no autonomy
nodes), source ROS and run from `autonomy/ros2`:

```bash
export GZ_SIM_RESOURCE_PATH="$PWD/src/lhr_gazebo/models${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
gz sim src/lhr_gazebo/worlds/skidpad_seed1.sdf
```

The preview has 17 inner and 13 outer cones per circle, small orange entry/
exit lanes and large orange gates. Orange cone spacing and lane lengths are
illustrative sim choices. The spawn's foremost vehicle envelope is 15 m before
the center line, using the current `lhr_vehicle` model footprint. Do not use
the autocross planner to drive this world.

## Skidpad event

After building and sourcing the workspace:

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py mission:=skidpad track_style:=skidpad \
  perception:=sim estimator:=truth enable_metrics:=false
```

The view defaults to 15 m / 240 degrees, sim cone accumulation is off, and
only one planner owns the path. After Go, the car creeps until colored lane
and timing-gate observations validate entry, stops for acquisition, then
drives two clockwise right laps and two counterclockwise left laps. It exits
and stops, then the mission reaches `FINISHED`. RViz shows the visible cones,
fitted circles and the short active path sent to pure pursuit.

The large orange pairs now bracket the shared center timing line at y=0.
Their +/-0.5 m offsets and small-cone lane spacing are simulation placement
choices; the center timing line itself comes from D.10.1.3. These pairs no
longer mark illustrative gates at y=+/-7 m.
The fitter now initializes from detected cone chords and the known center
separation, rather than assuming a center from the staging distance. It uses
the initial vehicle heading to resolve orientation. Acquisition reports include
the number of retained center candidates and refined pose hypotheses.

`perception:=lidar` applies the same planner view filter and detector range;
there is no orange classification in that pipeline, so acquisition cannot
creep or authorize event laps without independently verified orange and
blue/yellow classifications. The demo above uses
ideal labelled detections and truth odometry; it is not the no-shortcuts gate.

All [acquisition thresholds](../lhr_track_builder/README.md#skidpad-planning)
are launch arguments with matching defaults/types. `fov_deg` / `max_range_m`
retain 200 / 20 defaults for other missions. `accumulate_detections` defaults
to true elsewhere; skidpad defaults to false. Gazebo sets `closed_path` to
false for all missions. `require_planner_limit` defaults to true for skidpad
and false otherwise; `planner_timeout_sec` is 0.5. Do not disable the required
cap for acquisition. Chaining parameters apply only to the autocross planner.


In RViz, inferred left cones are blue, inferred right cones are yellow, and
cones awaiting evidence are orange. During a sharp turn:

1. each physical cone should have one marker;
2. a marker should remain fixed in the map frame;
3. old orange markers should disappear when their side becomes known; and
4. the green centerline should remain a single ordered band without branches.

## Verification

```bash
cd autonomy/ros2
colcon test --packages-select lhr_gazebo
colcon test-result --verbose
```

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

Camera classifier launch tests cover defaults and overrides in both perception
and estimator modes. Evaluated parameters use JSON encoding for ROS CLI startup
so arrays keep their type. Each case shuts down ROS even if node startup fails,
so a startup error does not cascade into later cases.

## Stereo model

The default generated `models/fsae_vehicle/model.sdf` has no cameras, so
LiDAR-only runs pay no camera initialization or rendering cost.
`model_stereo.sdf` adds one ZED 2i rig with the 2.1 mm lens: two ideal
RGB pinhole sensors, parallel axes, and a 0.12 m local-y separation. Both
sensors share `zed2i_link`, so rotating the mount preserves Gazebo's stereo
baseline calculation. Geometry and optics come from `lhr_vehicle`'s YAML;
regenerate both models after changing them:

```bash
cd autonomy/ros2
python3 src/lhr_gazebo/scripts/generate_vehicle_model.py
python3 src/lhr_gazebo/scripts/generate_vehicle_model.py --check
./scripts/build.sh
```

Commit both generated SDFs. The generator and freshness tests check both
variants against `vehicle.yaml` and the templates.

The camera-only link has no collision and uses 0.001 kg mass / 0.000001 kg m2
diagonal inertia for simulation bookkeeping. The initial mount and optics
are approximations awaiting measurement and physical device calibration.

## Stereo images in ROS/RViz

Enable the camera independently of perception and state estimation:

```bash
./scripts/run_gazebo_demo.sh track_style:=oval perception:=lidar \
  estimator:=truth enable_stereo_camera:=true
```

`enable_stereo_camera` defaults to `false`. Enabling it selects the stereo
model, starts a separate lazy image/calibration bridge, publishes the rig's
static optical transforms, and selects an RViz configuration with left/right
Image panels. The option
works with both `perception:=sim` / `lidar` and `estimator:=truth` / `ekf`.
The launch prepends a temporary model resource directory that maps the existing
`model://fsae_vehicle` URI to `model_stereo.sdf` and the shared meshes. It removes
that directory on shutdown. Worlds, including custom `world:=...` files, keep
their existing vehicle URI; the installed default model is never modified.
Camera geometry and optics are model configuration, not ROS node parameters:
edit `lhr_vehicle/config/vehicle.yaml`, regenerate, rebuild, and relaunch.

| ROS topic | Type |
| --- | --- |
| `/lhr/sensor/zed2i/left/image_rect` | `sensor_msgs/msg/Image` |
| `/lhr/sensor/zed2i/right/image_rect` | `sensor_msgs/msg/Image` |
| `/lhr/sensor/zed2i/left/camera_info` | `sensor_msgs/msg/CameraInfo` |
| `/lhr/sensor/zed2i/right/camera_info` | `sensor_msgs/msg/CameraInfo` |

Use sensor-data QoS (best effort, volatile) for consumers. Images retain
Gazebo timestamps and optical frame IDs. Both views are ideal rectified RGB;
`CameraInfo` describes those rendered intrinsics, with right `P[3] = -fx * B`.
The static chain is `base_link -> zed2i_link -> left/right camera frames ->
optical frames` (optical x right, y down, z forward). The mount transform
continues to attach to the estimator-owned `base_link` in EKF mode.

The 30 Hz configured rate is in simulation time. Software rendering can
reduce wall-clock throughput. The lazy bridges stop requesting images without
subscribers, but enabled cameras still render in Gazebo. Disable stereo to
remove that load entirely.
Camera color segmentation can now consume the left stream; LiDAR fusion
remains later work.

### Camera color debug view

Opt into labeled blue/yellow/orange candidate regions:

```bash
./scripts/run_gazebo_demo.sh enable_stereo_camera:=true \
  enable_camera_color_classifier:=true
```

`enable_camera_color_classifier` defaults to `false` and requires
`enable_stereo_camera:=true`. It starts `lhr_perception`'s
`camera_color_classifier` in either perception mode and with either estimator.
The stereo RViz configuration includes an Image display for
`/lhr/perception/cone_colors/debug_image`, using Best Effort reliability.
It receives no frames until classification is enabled. The existing left/right
camera panels stay available. Add the same display when using a custom config.

All classifier settings are forwarded through launch: `cone_color_min_region_area`
(integer, default `25`), `cone_color_blue_hsv` (default
`[100, 100, 50, 130, 255, 255]`), `cone_color_yellow_hsv` (default
`[20, 100, 50, 35, 255, 255]`), and `cone_color_orange_hsv` (default
`[5, 100, 50, 19, 255, 255]`). HSV bounds are six-element integer arrays.
See [classifier usage and limitations](../lhr_perception/README.md#camera-color-classification)
for overrides and input/output details. These image labels do not yet change
the cone map or driving behavior.

The model explicitly publishes vehicle TF on `/model/fsae_vehicle/tf` to
match the bridge. Harmonic's default is `/model/fsae_vehicle/pose`; leaving
that implicit prevents `map -> base_link` from reaching ROS in truth mode.

## Stereo validation

### Disabled-camera rendering regression

Checked on 2026-10-20 for [#123](https://github.com/LonghornRacingElectric/lhre/issues/123)
in the Apple Silicon Docker/Colima environment (6 CPUs, 8 GiB, software OpenGL).
Both runs used `track_style:=oval perception:=lidar estimator:=ekf`,
`gui:=false rviz:=false enable_metrics:=false enable_stereo_camera:=false`.
After 10 simulation seconds of warmup, `/lhr/lidar/points` header timestamps
were measured for about 90 simulation seconds:

| Model | LiDAR rate | Longest gap | Gazebo camera topics |
| --- | --- | --- | --- |
| Previous committed model, cameras always present | 6.26 Hz | 1.30 s | 4 |
| Camera-free default model | 7.97 Hz | 0.70 s | 0 |

The fixed run had no ROS camera messages either. These are same-session
measurements; absolute rates depend on host load and software rendering.
An enabled-stereo run delivered both 1280x720 RGB streams and both calibration
streams. Intrinsics, the right projection's baseline, and both optical
transforms from `base_link` and `map` passed runtime checks.
The full workspace build and 248 tests passed, including enabled/disabled
launch selection in both perception and estimator modes, shutdown cleanup,
and rejection of either stale generated SDF. Both models passed `gz sdf -k`.

### Original stereo interface check

Checked on 2026-10-11 in the repository's Ubuntu 24.04 / ROS 2 Jazzy /
Gazebo Harmonic Docker image on Apple Silicon with software OpenGL:

- Full workspace `colcon test`: 208 tests, no errors/failures/skips.
- Generated model passes `gz sdf -k` and the model freshness check.
- Launch evaluation covers enabled/disabled/default stereo across both
  perception modes and truth/EKF estimation; 15 stereo integration tests pass.
- A stationary dynamic vehicle in a zero-gravity target scene yielded 100
  exact-timestamp sets of both 1280x720 RGB images and both calibration messages.
  Intrinsics, distortion/rectification, optical headers, and baseline passed.
  Both optical frames were reachable from `base_link` and `map`.
- The original oval track with LiDAR perception and EKF yielded another 100
  matching image/calibration sets with optical TF reachable from `map`.
  RViz displayed both rendered cone views.
- A 1 m green target face at 5 m yielded 11 px horizontal disparity against
  10.76 px predicted, with zero vertical disparity. RViz showed both images
  and a valid map fixed frame.

The 100 paired sets took 20.94 wall-clock seconds including startup. This
measures matched-set delivery through the checker, not sensor generation
frequency; dropped samples and software rendering limit throughput. Target
scene statistics reported a real-time factor near 1 after startup. The
oval/EKF run delivered 100 matched sets in 30.36 wall-clock seconds with
RViz active. After RViz and the checker stopped, Gazebo reported no image
subscribers, confirming the lazy bridge disconnected. Physical
calibration, rolling shutter/exposure effects, and performance on the eventual
compute platform remain unvalidated. This is camera-interface acceptance,
not validation of camera-based cone detection or driving behavior.

The stationary target fixture is `test/stereo_target.sdf`. For a visual repeat
from the ROS workspace on a machine with a display:

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py \
  world:="$(pwd)/src/lhr_gazebo/test/stereo_target.sdf" \
  enable_stereo_camera:=true auto_go:=false estimator:=truth
```

The fixture disables gravity to hold the dynamic vehicle stationary while
its odometry plugin publishes TF. Its green target front face is at world
x=6.8 m, 5 m in front of the current assumed rig center, so changed mount
geometry requires recomputing the measured target distance.

Camera extrinsics are fixed relative to `base_link`. The existing vehicle
state/TF is planar, so the map-frame camera pose does not fully represent
physics roll/pitch. Future 3D projection/fusion needs a 6-DoF vehicle pose.

## Reference scoring

Metrics uses the actual selected world file, including an explicit `world:=...`
override, as its fixed truth source. The supported generated worlds contain
ordered `cone_blue_N` and `cone_yellow_N` includes. Metrics never uses the
planner's path or feeds this reference back to perception/control.

| Argument | Default | Meaning |
| --- | --- | --- |
| `reference_world` | empty | Empty resolves to the selected world; override only for a matching reference file |
| `reference_closed` | mission/style dependent | False for acceleration, true for circuits; explicit overrides remain supported |
| `off_track_threshold` | `2.0` | Legacy progress-continuity scale, metres; validity uses truth boundaries and the vehicle envelope |
| `start_radius` | `2.0` | Fixed crossing-gate half-width, metres |
| `min_lap_time` | `5.0` | Minimum simulation seconds after driving onset |
| `min_progress_fraction` | `0.98` | Required signed circuit progress |
| `lap_start_speed` | `0.1` | Speed at which driving timing starts, m/s |
| `max_odom_gap_sec` | `1.0` | Maximum gap during a scored circuit |
| `output_csv` | `data/metrics.csv` | Run row, including reference hash and invalidation reason |

Scoring arguments are typed and forwarded in both perception branches and
with truth or EKF estimation. EKF mode scores `/lhr/vehicle/odom_truth`; truth
mode scores the plant-owned `/lhr/vehicle/odom`. Estimates only feed error
measurements. Rows also record the mission the launch actually runs (always
acceleration on the acceleration track), Gazebo's scenario, plant, seed, track style,
waypoints, perception, estimator, and selected estimator perturbations. `reference_closed:=false` scores an open course
without inventing a return segment or a circuit lap. Missing or invalid world
geometry fails rather than silently using a different course. See the
[metrics contract](../lhr_metrics/README.md#reference-and-lap-contract).

Metrics counts conservative geometric contacts against nominal cone bases,
including orange cones, and checks the whole YAML vehicle envelope against the
selected world boundaries. This uses truth poses, not Gazebo contact forces;
see the [scoring model](../lhr_metrics/README.md#footprint-boundaries-and-contacts).
