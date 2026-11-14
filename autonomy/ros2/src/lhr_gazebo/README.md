# lhr_gazebo

Gazebo Harmonic vehicle physics, sensor bridging, and demo launch. Run
`ros2 launch lhr_gazebo gazebo_demo.launch.py` in a supported ROS environment.
`perception:=sim` uses simplified cone detections; `perception:=lidar` uses
[lhr_perception](../lhr_perception/README.md) and boundary planning.
Both perception modes select open-path control: the track builder publishes
a forward prefix, so lookahead and curvature must not connect its ends.
See [the workspace reference](../../README.md) for launch arguments and
vehicle physics.

## Native IMU noise and bias

`estimator:=ekf` now defaults to `use_wheel_speeds:=false`. It does not fuse
wheel speeds or start Gazebo's truth-derived wheel sensor node. Both perception
branches forward the boolean to the EKF. `use_wheel_speeds:=true` restores the
legacy diagnostic configuration used by the historical #171 report below;
`wheel_scale_error` has no effect with wheel fusion off. Metrics records the
selected wheel-fusion mode. IMU-only calibration experiments are not a claim
that the existing planar filter meets the 0.5 m gate without wheel updates.

The [2026-11-08 validation report](../../../testing/2026-11-08-native-imu/README.md)
records stationary sensor measurements, matched noisy/clean gate trials and
independent bag cross-checks. Native noise exposes EKF accuracy failures;
the report retains LiDAR-stale and cone-contact failures separately.

Both generated vehicle models apply native Gazebo Gaussian noise and startup
bias to all gyro and accelerometer axes. Defaults and their datasheet
conversion assumptions live in [lhr_vehicle](../lhr_vehicle/README.md#native-gazebo-imu).
They apply in both perception modes, with either estimator. The ROS bridge
still delivers the physics IMU directly to `/lhr/imu/data`; no ROS noise node
or truth-derived replacement is inserted.
Orientation remains Gazebo's ideal quaternion; the EKF consumes acceleration
and angular rate, not that quaternion.

| Argument | Default | Effect |
| --- | --- | --- |
| `imu_noise` | `true` | Enable configured white noise and startup bias; `false` zeros both on all six axes |
| `gyro_bias` | `0.0` rad/s | Additional signed constant offset on the native sensor's z gyro, independent of `imu_noise` |
| `seed` | `1` | Course seed and Gazebo `--seed` random-generator seed |

`gyro_bias:=0.03` now changes the sensor the EKF actually receives. It is
implemented as the z-axis Gaussian **mean**, since Gazebo randomizes the
sign of its sampled startup bias. It adds to the YAML startup bias when
`imu_noise:=true`; it remains active when `imu_noise:=false`.
Overrides are applied at startup using a temporary vehicle resource overlay,
including the stereo variant when enabled. Installed models are untouched
and the overlay is removed at launch shutdown. Controls are startup settings,
not live ROS parameter updates.

Metrics records `imu_noise`, the additional `gyro_bias`, `seed`, and
`vehicle_sha256`. The latter identifies the configured native noise values.
The shared metrics node defaults `imu_noise` to false for other plants;
the Gazebo launch explicitly enables it. Inertial sim still synthesizes
truth-derived wheel speeds only with `use_wheel_speeds:=true`; wheel slip
fidelity in those optional measurements is unchanged.

Matched noise-free and native-noise runs:

```bash
./scripts/run_gazebo_demo.sh gui:=false rviz:=false perception:=lidar \
  estimator:=ekf safety:=true seed:=1 imu_noise:=false output_csv:=data/imu-clean.csv
./scripts/run_gazebo_demo.sh gui:=false rviz:=false perception:=lidar \
  estimator:=ekf safety:=true seed:=1 imu_noise:=true output_csv:=data/imu-native.csv
```

After building and sourcing `install/setup.bash`, measure the actual bridged
vehicle IMU at standstill, with five simulation seconds discarded for settling:

```bash
python3 src/lhr_gazebo/scripts/validate_imu.py data/imu-validation
```

This runs clean, native-noise, +0.03 and −0.03 rad/s fault trials, writes
timestamped six-axis samples, launch arguments/logs, `stationary.csv` and
`checks.json`, and exits nonzero if cadence, noise spread or fault sign fails.
It requires ROS, Gazebo and the normal server rendering environment; run it
without another demo publishing on the same ROS domain.
For driving comparisons, use the existing
[gate runner and scorer](../../../testing/tools/gate/README.md) in separate
output directories with `EXTRA="imu_noise:=false"` and
`EXTRA="imu_noise:=true"`. Keep all other settings and the EKF fixed. Course
seed also seeds Gazebo, but ROS scheduling can still change trajectories;
repeat runs and retain failures rather than claiming bitwise determinism.

Combine scored campaigns into `comparison.csv` and `position-error.png`:

```bash
python3 src/lhr_gazebo/scripts/compare_imu.py data/imu-comparison \
  --campaign clean=data/imu-clean-gate --campaign native=data/imu-native-gate
```

The comparison retains every gate row and its failed checks, including
missing metrics. It reads mean errors from the original metrics CSV because
the gate scorer's output includes only the maximum position error.
Plotting requires Matplotlib; it does not require a running ROS graph.

For isolated estimator experiments, `scripts/record_imu_reference.py` records
the native physics IMU, commands, mission status and a separate truth pose.
It privately removes GPU LiDAR/rendering from a copied world/model, retaining
vehicle and IMU physics. Its reference controller uses truth and synthetic
cone detections to reproduce a trajectory; this is an input-data capture,
not a closed-loop IMU-only driving success or a perception gate. Offline
estimators must never read truth except for the initial surveyed-pose proxy
and evaluation. Raw captures stay under gitignored `data/`.
`--full-rendering` instead selects the unchanged installed world and its normal
model/noise overlay, to cross-check that the lightweight harness did not cause
a sensor discrepancy. Both paths record the exact launch arguments.

For acceleration-path diagnosis, `--capture-native` additionally saves
`native-imu.csv` directly from Gazebo Transport, before the ROS bridge.
`--stop-sim-time 16` ends a short input capture. Private-model experiments can
set `--imu-rate 1000`, `--direct-drive` (old failure control), or `--force-drive`
(explicit configured force control); none changes the installed model.
Force control is now the installed default, using the values in
`vehicle.yaml`. `--direct-drive` and `--force-drive` are mutually exclusive.
Overrides cannot be combined with `--full-rendering`. Native recording requires the Gazebo
Transport 13 and Messages 10 Python bindings supplied by the Jazzy vendors.

`scripts/check_imu_acceleration.py OUT.csv CAPTURE...` checks timestamp-matched
native/ROS equality and compares integrated forward IMU acceleration against
the independent speed change over simulation seconds 12–15. It requires
noise-free acceleration captures and a stationary 2–7 s offset interval.
Its expected controls are missing acceleration for direct velocity at 100 Hz,
and recovered acceleration for direct velocity at 1000 Hz or force control at
100 Hz. A diagnostic pass confirms those controls, not a driving gate pass.
The [acceleration-path results](../../../testing/2026-11-08-native-imu/README.md#acceleration-path-diagnosis)
show that the 100 Hz sensor misses pulses from direct joint-velocity actuation.
Increasing the rate reveals the pulses but does not supply an antialiasing
filter; force control recovers a smoother signal. Production actuation is now
force-based by default, with P=10 and ±100 N·m per-wheel torque clamps from
`simulation.gazebo_drive` in `vehicle.yaml`. These are simulator assumptions;
see [vehicle configuration](../lhr_vehicle/README.md#gazebo-actuation).
Both generated models now use a 1000 Hz native sensor, matching the default
1 ms physics step. The EKF integrates each stamped sample and publishes
odometry at 50 Hz. Noise bandwidth scales from 50 to 500 Hz with this rate:
per-sample noise increases by sqrt(10), preserving its integrated intensity.
Datasheet densities and startup bias distributions are unchanged. This is
simulator sampling, not a claim about the physical Mid-360's output cadence.
Steering control and wheel command topics are unchanged. Torque-based tracking
adds a small speed lag; braking commands request zero wheel speed through the
same bounded controller. This does not calibrate motor or tire dynamics.
`--post-terminal-sim-sec 3` records three simulation seconds after mission
termination so braking checks do not depend on rendering wall-clock speed.
For a complete capture, `scripts/check_imu_acceleration.py OUT.json CAPTURE...
--motion` verifies six-axis stationary noise spread, native/ROS equality and
sample gaps. Acceleration captures must finish and stop, with integrated
acceleration matching speed change over fixed 12–15 s and 28–32 s intervals.
Oval captures must cover 44 s and match integrated yaw over 12–44 s. Offset
subtraction is offline diagnostic processing; it does not enable calibration
in the running estimator. See the
[force-control fix results](../../../testing/2026-11-08-native-imu/README.md#force-based-actuation-fix)
for measurements, assumptions and commands.

For the stopped-car case, `scripts/record_ekf_stop.py OUT` reproduces the
wheel-free autocross seed-3 LiDAR/EKF/safety configuration headlessly for
30 simulation seconds. It records the primary native IMU, bridged IMU,
estimated and physics odometry, commands, mission/safety status and clock.
Use an isolated ROS domain and Gazebo partition. Optional `--high-rate-probe`
adds a separate noiseless 1 kHz IMU and 3D reference publisher to a private
copy of the otherwise unchanged vehicle/world; production sensors and
estimator settings are retained. `--noise false` is a comparison control.

`scripts/analyze_ekf_stop.py CAPTURE OUT` reports estimated travel during
physics-stationary intervals and native/ROS equality. If probe data exist,
it compares world-frame velocity impulses against independent 3D sensor-point
motion over fixed seed-3 windows. It uses ideal attitude, initial world-frame
alignment and offline offsets only for diagnosis, never for fusion. The
noiseless probe is also sampled at the production timestamps to separate
aliasing from noise. The probe analysis requires capture through 29 s. See
the [stopped-car investigation](../../../testing/2026-11-08-native-imu/README.md#stopped-car-investigation):
the investigation describes the former 100 Hz / timer-predicted failure and
its follow-up 1000 Hz / timestamp-integration change. Force control and higher
sample rate are complementary. This harness changes no runtime filter behavior
and does not validate a standstill detector. Bias and attitude compensation
remain separate work.

For cone/path causal experiments, add `--perception-study` to the stop
recorder. It records compressed raw clouds, live cone maps, centerlines and
lookahead targets; captures actual detector/planner/controller parameters;
and adds passive contact probes to the private model. `contact-audit.json`
must show delivered contacts before an empty cone-contact list is interpreted
as no observed cone contact. Ground contact traffic verifies that the probes
are active. The contact topic is configured under `<contact>` for Harmonic.
`--estimator truth` is a diagnostic localization control, never an on-car
configuration. Captures stop if free disk space drops below 150 MiB.

`scripts/analyze_cone_path.py CAPTURE OUT` replays identical clouds through
the production detector with recorded EKF poses, physics poses, a fixed rigid
frame transform, and an EKF/current-scan-only map. It diagnoses persistent-map
association using physical cone identities only as offline labels, and tests
the production planner with exact cone geometry. Clearances use a conservative
vehicle envelope and cone circumscribed circles over the next 8 m of path;
negative clearance is a risk indicator, not proof of physics contact. Live-map
checks supplement the replays, which do not reproduce asynchronous scheduling
bit for bit. See the
[cone/path study](../../../testing/2026-11-08-native-imu/README.md#cone-path-causal-study)
for hypotheses, contact events, controls, results and remaining limitations.
`scripts/plot_cone_path.py ANALYSIS OUT` renders the replay error traces and a
pre-contact vehicle-frame view; it requires Matplotlib and the sourced ROS
environment. The local view overlays physical cones with accumulated cone
positions relative to the estimated car, making changing map error visible.

`scripts/evaluate_imu_only.py OUT CAPTURE...` replays the recorded IMU through
the existing wheel-free filter and compares raw, gyro-calibrated and
known-level forward-offset-calibrated inputs. It never reads the IMU's ideal
orientation or derives speed from truth. The first pose is a one-time survey
proxy; truth after that is used for evaluation. A planar mount-lever-arm
variant is a diagnostic only. Forward-offset subtraction at one known-level
stationary pose is not a general accelerometer/tilt calibration, and these
offline experiments do not enable live calibration or bias-state estimation.

Independently cross-check the error columns from stamped estimate/truth poses
in the MCAP bags (requires the sourced ROS environment):

```bash
python3 src/lhr_gazebo/scripts/verify_imu_bags.py data/imu-comparison/bag-crosscheck.csv \
  --campaign clean=data/imu-clean-gate --campaign native=data/imu-native-gate
```

This uses NumPy interpolation, not the metrics accumulator or `TruthAligner`.
It selects the CSV's scored time interval, unwraps truth yaw before
interpolation, and compares mean/max position and mean yaw errors. It exits
nonzero on differences above 0.02 m or 0.002 rad, allowing endpoint rounding
and DDS delivery differences. Missing error rows are not cross-checked;
the gate scorer and comparison CSV still retain those failures.

## LiDAR coordinates

Gazebo preserves main's level LiDAR mount at z=0.55 m and its generated
cloud frame. The detector reads that mount from vehicle YAML rather than
requiring a new static TF. Its height band stays in the sensor frame.
The kinematic demo uses scan-time TF and base-frame heights by default;
Gazebo keeps the values below for either cloud source.

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

The default `lidar_source:=gazebo` retains main's repeating VLP-16-style grid:
1800 × 16 rays at 10 Hz, 360° horizontal and −15° to +15° vertical coverage,
0.9–100 m range and 0.003 m range resolution. Select the caster below for
imported Livox scan directions. Vendor-pattern GPU sampling is deferred to a
separate experiment.

### GPU grid rollback

**The experimental GPU grid has been reverted.** The 500 × 40 grid contacted
`cone_blue_10` in two isolated autocross seed-2 runs; the previous main GPU
model and the vendor-pattern caster passed that same world. The historical
official bounded gate also reports cone contacts on GPU seeds 2 and 3, while
the caster passes seeds 1–3 and acceleration. PR #133 now preserves main's
GPU sampling geometry; the underlying observation failure remains undiagnosed.
The historical failures use whole-vehicle/cone-base scoring, not measured
collision forces. Planner and detector defaults are retained. The local
integrated rollback passes focused seed-2 and seed-3 driving checks with the
diagnostic timeout; see the [integration results and retained failures](../../../testing/2026-10-24-pr133-integration/README.md#integrated-main-verification-2026-10-25).

The unchanged 0.3 s LiDAR timeout still trips `LIDAR_STALE` locally on both
the integrated rollback and clean main `0ab0357`, with 0.4–0.5 s acquisition
gaps. GPU runs using
`safety_lidar_timeout_s:=1.5` are diagnostics and do not pass the default-timeout
gate. The integrated tree retains main's camera-free default model and
startup-readiness gating; the remaining local delivery cause is not established. See
[#123](https://github.com/LonghornRacingElectric/lhre/issues/123) and the
[validation record](../../../testing/2026-10-24-pr133-integration/README.md).

### Range noise

The GPU LiDAR applies independent, zero-mean Gaussian noise along each beam
before publishing the point cloud. Its standard deviation comes from
`sensors.lidar.range_noise_std_m` in `lhr_vehicle/config/vehicle.yaml`:
`0.02` m (2 cm) by default. This rounds the upper end of the 10/4 acceptance
test's 0.9-1.9 cm floor-patch residuals and matches the existing
[Mid-360 simulator](../lhr_lidar_sim/README.md)'s range-noise setting.
Floor-plane residuals are not a direct along-beam calibration; this is a
provisional noise scale, not a calibration of the GPU grid or caster.
See the [acceptance log](../../../testing/2026-10-04-mid360-acceptance/log.md#outdoors-driveway-in-front-of-the-garage-from-1637).

The setting must be finite and non-negative. Set it to `0.0` for an ideal-range
comparison, then regenerate the model and rebuild as below. It is model
configuration, not a ROS node parameter or launch argument. Sensor range
limits still apply; angular error, dropout and reflectivity are separate models.

#### Validation (2026-10-23)

Historical VLP-16-grid checks against main `1f694cc`, before the default
GPU grid replacement, in the ROS 2 Jazzy / Gazebo Harmonic container
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
Commit both regenerated models (`model.sdf` and `model_stereo.sdf`) alongside
the source. The model consistency tests
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

## Mid-360 cloud source and Foxglove

`lidar_source:=gazebo` selects the GPU sensor by default. To use the loaded
world with vendor-pattern casting and an optional viewer:

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py \
  track_style:=autocross perception:=lidar estimator:=ekf \
  lidar_source:=mid360 gui:=false rviz:=false foxglove:=true foxglove_port:=8767
```

Connect to `ws://localhost:8767` and import `autonomy/ros2/foxglove/lhr_gazebo.json`.
See the [Gazebo layout guide](../../foxglove/README.md) for panel meanings,
recording, validation and a visual checklist. The viewer includes primitive
car markers; no mesh server is needed. RViz is optional.

With `foxglove:=true`, `world_viz` reads the selected rendered world and
publishes translucent nominal cone envelopes on `/lhr/viewer/cones_truth`
and the fixed reference path on `/lhr/viewer/centerline_truth`.
These are viewer-only topics: LiDAR perception and planning still use
`/lhr/sensor/cones_detected`, and `/lhr/track/cones` remains absent in LiDAR
mode. World/acceleration selection is fixed by launch, not an independent
viewer setting. Foxglove works in either perception mode and defaults to
`foxglove:=false foxglove_port:=8765`. The pose comparison layout expects
`estimator:=ekf`, and its metrics panel expects `enable_metrics:=true`.

Custom WebSocket clients should offer `foxglove.sdk.v1` as well as the legacy
`foxglove.websocket.v1`. The installed SDK-based bridge accepted the former
and streamed PointCloud2; a legacy-only probe received HTTP 400. The
[upstream handshake](https://github.com/foxglove/foxglove-sdk/blob/main/rust/foxglove/src/websocket/handshake.rs)
rejects clients without a supported subprotocol. This protocol check does not
verify the graphical layout.

`mid360` requires `perception:=lidar`. The caster reads the resolved SDF and
Gazebo truth pose; perception still receives estimated odometry under EKF.
Unsupported caster worlds fail in launch setup before processes start; the
standalone sensor also validates them. Flat ground and nominal upright cone
includes are supported, not arbitrary obstacles or terrain.
An explicit missing `world:=...` fails instead of falling back to another track.
With truth-mode odometry faults the caster reads the bridge's clean
`/lhr/vehicle/odom_raw`; with EKF it reads `/lhr/vehicle/odom_truth`.
Only the driving odometry passes through the fault injector, so an odometry
fault cannot remove cones from the synthetic sensor's input scene.
Only the selected cloud owns `/lhr/lidar/points`; the GPU comparison moves to
`/lhr/lidar/gazebo_points`. The existing driving nodes and defaults are retained.
`lidar_drop` intercepts the selected producer, including Mid-360.

The internal read-only `world_sdf` is bound to launch's `world`, and `seed`
reaches the caster as an integer. Mount, motion and clutter study settings are
fixed in world mode; see [sensor limits](../lhr_lidar_sim/README.md#gazebo-world-input).

The following typed startup arguments affect only `lidar_source:=mid360`:

| Argument | Default |
| --- | --- |
| `mid360_frame_rate_hz` | `10.0` |
| `mid360_point_rate_hz` | `200000.0` |
| `mid360_max_range_m` | `40.0` |
| `mid360_range_noise_std_m` | vehicle YAML, currently `0.02` m |
| `mid360_dropout_rate` | `0.0` |
| `mid360_elevation_profile` | `livox` (`rosette` / `uniform` are approximations) |
| `mid360_return_profile` | `baseline` (`acceptance_overcast` is an empirical profile) |

For example, `mid360_range_noise_std_m:=0 mid360_max_range_m:=30` starts the
caster with ideal ranges limited to 30 m. The noise default shares the GPU
sensor's vehicle-YAML value; this override affects only the caster. GPU noise
still requires model regeneration. Record the source and effective settings
with comparison results; the generic `max_range_m` controls `sensor_sim`, not
the caster. These modes do not share a physical accuracy guarantee.

World mode intentionally exposes no mount, motion-distortion or clutter launch
controls: they are fixed to the shared mount, snapshot scans and no synthetic
clutter. The internal world path follows `world`; the 0.1 m sensor minimum is
fixed. Study those alternatives in marker mode instead.

On Wayland with X11 available, Gazebo/RViz select `xcb` unless another backend
is explicitly chosen. Gazebo uses the packaged Fontconfig configuration;
an explicit `FONTCONFIG_FILE` takes precedence. These overrides are scoped
to the launched processes. The packaged font configuration caches under the
user's XDG cache directory rather than rescanning without a cache on each start.
It scans only `/usr/share/fonts` and deliberately does not include the system
`fonts.conf`, which would re-enable user fonts and matching rules. A GUI
segfault ending in `QFontEngineMulti` / `FcCharSetHasChar` can otherwise cause
Gazebo's parent process to print `Escalating to SIGKILL on [Gazebo Sim Server]`:
that final message is server cleanup after the GUI crash, not its cause.
This scoped configuration leaves other applications and your installed fonts
unchanged. An explicit caller-provided `FONTCONFIG_FILE` can reintroduce the
problem; remove that override to use the packaged configuration.

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
| `lidar_drop` | The selected source publishes `/lhr/lidar/points_raw`; the injector stops relaying |
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

Source tests cover typed startup, truth/EKF wiring, unique TF ownership,
cloud-to-detector output, supported geometry and active-source fault injection.
Culling tests match unculled intersections exactly. Live checks cover motion,
Foxglove and emergency stops; they do not establish full-lap acceptance.
Reported scan rates use simulation time; wall-time rates depend on Gazebo speed.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.
Interrupt-time callback tracebacks remain observable in unchanged main-branch
nodes, including the default GPU source configuration.

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
Camera color segmentation and opt-in LiDAR inspection-map fusion consume the
left stream. In fusion mode, classified camera/LiDAR labels feed boundary planning.

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

### Camera–LiDAR inspection map

After rebuilding, run:

```bash
./scripts/run_gazebo_demo.sh perception:=lidar estimator:=truth \
  enable_stereo_camera:=true enable_camera_lidar_fusion:=true
```

`enable_camera_lidar_fusion` defaults to `false`. It requires stereo and
`perception:=lidar`, works with truth or EKF estimation, and independently
consumes raw left images, matching CameraInfo, stamped TF, and LiDAR maps.
It does not require the debug classifier. Enable **CameraFusedCones** in the
stereo RViz config to display `/lhr/perception/cones_fused`; its default is
hidden, with reliable/transient-local QoS. Hide the original cone display
when inspecting the colored markers. In fusion mode the planner reads
`/lhr/perception/cones_classified` with color-aware boundary pairing. This
output prioritizes fresh confirmed camera observations and falls back to
the current LiDAR side.
Enable **ClassifiedCones** to inspect it. Fusion automatically enables the
existing geometry classifier for fallback, including when the explicit
`lidar_classify_sides` argument is false. Without fusion, the planner topic
and pairing/side-inference choices retain their previous behavior. Confirmed
orange cones keep their LiDAR side/unknown namespace in planning, including
during camera loss. Their physical color remains orange on the inspection map;
finish and exit-lane cones are not removed from the acceleration path.

All node settings are exposed with `fusion_` prefixes; color thresholds and
minimum area reuse `cone_color_*` with or without the debug classifier.
For example, add `fusion_pixel_margin:=3 fusion_max_color_range_m:=12`.
`fusion_camera_max_age_sec` defaults to `0.5` ROS seconds; after this interval
without a usable camera match, the timer republishes the LiDAR fallback even
without a new map. Missing/ambiguous matches use the current LiDAR side sooner.
See the [fusion contract and complete parameter table](../lhr_perception/README.md#cameralidar-inspection-map).
Launch tests cover disabled behavior, invalid combinations, and typed defaults
and overrides through actual node startup with both estimators. ROS launch
checks and synthetic image/TF/DDS-to-planner integration run in the isolated
Docker environment. Manual camera-color and autocross/EKF visuals were also
confirmed; the [validation record](#fusion-planner-validation-2026-11-01) states
the remaining limits.

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

### Fusion planner validation (2026-11-01)

An isolated Docker ROS 2 Jazzy workspace built the 11 packages through
`lhr_gazebo`. `colcon test` for `lhr_perception`, `lhr_track_builder`,
`lhr_gazebo`, and `lhr_vehicle` reported **465 tests, zero errors/failures/skips**.
This includes actual launch parameter evaluation/node startup in truth/EKF
modes and synthetic image/TF callbacks whose classified output travels over
DDS into the planner. The input callbacks are exercised directly in that test;
it does not verify camera/LiDAR ingress QoS or `/clock` delivery by itself.

A separate 40-second stationary oval check used `auto_go:=false`,
`perception:=lidar enable_stereo_camera:=true enable_camera_lidar_fusion:=true`,
truth odometry, software rendering on an isolated virtual display, and no
Gazebo GUI/RViz windows. The observer received 448 images, 985 calibrations,
617 inspection maps, 631 classified maps, and 162 planner paths. Inspection
maps included `blue_cones`, `yellow_cones`, and `unknown_cones`. Occasional
future-TF requests retried while map/path output continued. These are message
counts, not scored color accuracy or a throughput guarantee. The run was
stationary and did not verify moving-track or live camera-interruption behavior.

Celina subsequently confirmed the camera-color visual check in RViz and
reported that the manual autocross/EKF test looked good. This is user-reported
visual confirmation; no scored accuracy or driving metrics were recorded.
A separate outcome for live camera pause/resume has not been recorded here.

Review follow-up: a single late camera image now drops without clearing the
map, confirmed-orange evidence, or dynamic TF. Regression tests cover small
and large delays, duplicate frames, and continued processing of fresh frames;
map and ROS-clock regressions still reset the previous epoch. The affected
ROS suites passed **467 tests, zero errors/failures/skips** after this fix.

### Acceleration orange-geometry correction

The initial fusion planner output excluded confirmed orange cones. On the
acceleration course this removed the finish gate and the exit-lane corridor:
a generated-course regression reproduced a path endpoint at x=72.027 m,
before the 75 m finish. Orange now retains the detector's `left_cones`,
`right_cones`, or `cones` namespace for planning; its physical color remains
orange on the inspection topic. Blue/yellow camera priority is unchanged.

The regression confirms colors on the full generated acceleration layout and
checks that the path continues beyond the finish with both side-classified
and unclassified orange inputs. In a separate headless acceleration/EKF run
with fusion enabled, the mission reached `FINISHED` in 70.7 wall seconds. The
observer saw up to 18 confirmed orange cones on the inspection map, no
`orange_cones` on the classified topic, and planner paths extending to x=96 m.
Final estimated speed was 0.0024 m/s. This was one moving completion check
with safety/metrics disabled, not a repeated/scored MVP gate campaign; the
reviewer's full acceleration gate rerun is still needed.

## Rules-compliant courses

`track_style:=rules_autocross` uses the seeded piece-based
[track generator](../lhr_trackgen/README.md#rules-compliant-autocross).
Generate the matching world with `generate_world.py --style rules_autocross`
and pass its absolute path as `world:=...`. `width_m` (default 3.5) and
`cone_spacing_m` (default 2.0) reach the simplified cone publisher as typed
floats. Match them to the world's `--width` and `--cone-spacing` values.
For this style width is clear space between cone bases; both perception
branches give the planner the corresponding cone-center pairing width.
Legacy styles keep their width conventions and defaults. The generated
ground plane grows when necessary to contain long courses. Existing worlds
within the original 200 m plane serialize unchanged.
Metrics scores the actual world's paired centerline without a style-specific
route shortcut. These courses can expose failures in perception and planning;
passing the geometry checks does not imply the autonomy gate passes.

## Periodic GNSS

Opt into timestamped, uncertain position corrections while keeping wheel
fusion off:

```bash
./scripts/run_gazebo_demo.sh gui:=false rviz:=false \
  track_style:=autocross seed:=3 perception:=lidar estimator:=ekf \
  imu_noise:=true use_wheel_speeds:=false use_gnss:=true \
  safety:=true safety_lidar_timeout_s:=1.5 output_csv:=data/gnss.csv
```

Use `gui:=true rviz:=true` for visual inspection in a working desktop session.
Defaults are unchanged unless GNSS is enabled. Both `sim` and `lidar`
perception branches forward these typed arguments to the actual nodes:

| Argument | Default | Meaning |
| --- | --- | --- |
| `use_gnss` | `false` | Enable GNSS fusion; requires `estimator:=ekf` and wheel speeds off |
| `simulate_gnss` | `true` | Start the simulation-only fix publisher when fusion is enabled |
| `gnss_topic` / `gnss_frame_id` | `/lhr/gnss/fix` / `gnss_link` | NavSatFix topic and required antenna frame |
| `gnss_rate_hz` | YAML: 50.0 | Maximum simulated acquisition rate |
| `gnss_horizontal_stddev_m` | YAML: 0.8 | Independent east/north sigma, m |
| `gnss_vertical_stddev_m` | YAML: 1.2 | Up sigma, m; not fused |
| `gnss_latency_s` | YAML: 0.05 | Delivery delay, s; measurement stamp is retained |
| `gnss_origin_latitude_deg` / `gnss_origin_longitude_deg` / `gnss_origin_altitude_m` | 0/0/0 | Shared WGS84 datum, degrees and ellipsoid metres |
| `gnss_max_delay_s` | 1.0 | IMU replay history, s |
| `gnss_gate_chi2` | 25.0 | Squared Mahalanobis innovation gate |

The simulator's truth input is private plant wiring, not a public launch
argument. Its random stream uses the existing `seed`. Actual fix rate cannot
exceed the plant odometry rate (currently 50 Hz); it never repeats an old pose
to fabricate a higher-rate measurement. Hardware-like rate and error are
assumptions documented in [vehicle configuration](../lhr_vehicle/README.md#gnss-experiment-configuration).
`gnss_horizontal_stddev_m:=0.05` selects an assumed RTK-like comparison,
not a verified specification for the car's receiver.

For an external driver, use `simulate_gnss:=false`, configure a surveyed ENU
datum/mount and publish valid timestamped NavSatFix with known ENU covariance.
There is no physical receiver driver or BEVO-to-ROS adapter in this change.
The EKF does not receive truth fixes or GPS heading; Gazebo retains only its
existing one-time truth start-pose initialization. Corrections rewind/replay
IMU history; older TF and persistent cone observations are not rewritten.
See [filter behavior](../lhr_state_estimation/README.md#periodic-gnss-correction).

### Reproduce the GNSS comparison

After sourcing ROS and the workspace, use fresh capture directories and an
isolated ROS domain/Gazebo partition. Captures stop at 30 simulated seconds or
the recorder's wall-time/disk bound, and record fusion decisions and parameters:

```bash
export PATH=/usr/bin:$PATH
export ROS_DOMAIN_ID=103 GZ_PARTITION=gnss-reproduce
python3 src/lhr_gazebo/scripts/record_ekf_stop.py data/gps-baseline \
  --perception-study --stop-sim-time 30
python3 src/lhr_gazebo/scripts/record_ekf_stop.py data/gps-standard \
  --perception-study --stop-sim-time 30 --gnss
python3 src/lhr_gazebo/scripts/record_ekf_stop.py data/gps-rtk-like \
  --perception-study --stop-sim-time 30 --gnss --gnss-stddev-m 0.05
python3 src/lhr_gazebo/scripts/analyze_gnss.py \
  data/gps-standard data/gps-standard-analysis
```

The analyzer is specific to this seed-3 study and synthetic 0/0/0 datum. It
compares positions at matching sensor timestamps against a separate passive
3D physics probe, audits fix variance/rate/age and decisions, and reports an
equal 1–6 m physical route section. Use the same analyzer on each capture.
The [saved experiment](../../../testing/2026-11-08-native-imu/README.md#periodic-gnss-experiment)
shows lower pre-contact position error but continued collisions; this is not
a successful gate or physical-car validation. Large post-impact innovations
can be rejected, so accurate pre-contact results do not imply recovery after
an impact.

## Stationary cone registration diagnostic

`record_ekf_stop.py --stationary` forwards `auto_go:=false` to the existing
mission manager. It does not freeze the physics model, substitute truth
localization or disable sensing. Verify the recorded independent 3D physics
pose, command stream and mission state before labeling a window stationary.
This mode needs `--perception-study` for cone analysis. Actual detector,
planner, controller, mission-manager and EKF parameters are saved; the GNSS
simulator's parameters are saved when enabled.

From the sourced ROS workspace, use fresh directories and isolated domains:

```bash
export PATH=/usr/bin:$PATH
export ROS_DOMAIN_ID=116 GZ_PARTITION=stationary-cone-reproduce
python3 src/lhr_gazebo/scripts/record_ekf_stop.py data/still-gps \
  --stationary --perception-study --gnss --stop-sim-time 20
export ROS_DOMAIN_ID=119
python3 src/lhr_gazebo/scripts/analyze_stationary_cones.py \
  data/still-gps data/still-gps-analysis --start-s 2 --end-s 20
python3 src/lhr_gazebo/scripts/plot_stationary_cones.py \
  data/still-figures data/still-gps-analysis
```

Repeat the recorder without `--gnss` for a fresh IMU-only control, and with
`--gnss --gnss-stddev-m .05` for the assumed RTK-like profile. Hold seed,
noise, course and duration fixed. The analyzer replays **identical clouds**
through the real detector with EKF, physics and fixed-at-window-start poses,
and compares persistent mapping with a current-scan-only control. It measures
raw-return ROI scatter and fresh-detection scatter separately from pose
registration. Earlier startup scans remain in persistent history, even when
the scored window starts at 2 s.

These tools are specific to the seed-3 Gazebo study. Offline truth geometry
labels diagnostic landmarks, never live detections or estimator updates.
A 0.35 m nearest-cone radius separates geometry matches from displaced entries;
a separate birth-identity count detects displaced duplicate landmarks. Raw
ROI and fresh-centroid scatter use each cone's temporal median and at least
ten observations; these are precision metrics, not absolute accuracy. Live
processing may skip scans, so replaying every recorded cloud is a controlled
counterfactual, not exact live-map reconstruction. Both outputs are reported.

The [stationary experiment](../../../testing/2026-11-08-native-imu/README.md#stationary-cone-registration-experiment)
confirms a registration/accumulation problem while physical motion is zero.
Keeping LiDAR detection active at rest is useful; these diagnostics do not
add a production stationary detector, bias calibration, pose clamp or map fix.

For a desktop stationary inspection, append `auto_go:=false` to the normal
GPS visual command. The mission remains READY until an explicit go request.
Compare the cloud in RViz with Fixed Frame `base_link` versus `map`: only the
latter includes the estimated vehicle-to-map transform. This visualization
alone is not a motion/stationarity check; the recorded physics audit supplies
that evidence in simulation. The analyzer also reports mean native gyro-z
rate over the verified window, exposing a nonzero rate while physics rotation
is zero. It does not apply a calibration or subtract raw acceleration means.

## GPS heading-jump attribution

GNSS status includes before/after yaw and speed at measurement time and at
current time after delayed IMU replay. This is read-only instrumentation:
filter math, noise settings, gating and wheel-fusion behavior are unchanged.
See the [field definitions](../lhr_state_estimation/README.md#gnss-heading-update-diagnostics).

Capture a stationary GNSS run with the recorder above, then run:

```bash
python3 src/lhr_gazebo/scripts/audit_gnss_heading.py \
  data/still-gps data/still-gps-heading-audit --plot
```

The audit verifies zero physics motion, zero speed commands and no DRIVING
state in separate 2–5 s calibration and 8–20 s evaluation windows. It estimates
one gyro-z offset only from the calibration samples, then compares identical
inputs with GPS on/off and that offset applied/not applied. All offline
conditions restart at 5 s with the original one-time surveyed-start-pose proxy.
It does not use ideal IMU orientation or truth to fit an offset. A fifth,
explicit diagnostic subtracts the stationary forward-acceleration mean;
that combines bias/gravity projection at a fixed attitude and is not a
physical accelerometer calibration. Matplotlib is needed only with `--plot`.

The complete native IMU stream is used after checking equality with bridged
ROS IMU values at shared timestamps; the bag may miss messages received by
the live estimator. The audit separately checks original-start replay against
live odometry, excluding simultaneous output/correction timestamps where
callback order is ambiguous. Actual live per-correction jumps are reported
independently of all counterfactual replays. Old recordings without the new
status fields report live jump attribution as unknown.

The [saved attribution experiment](../../../testing/2026-11-08-native-imu/README.md#gps-heading-jitter-attribution)
confirms that GPS updates produce heading discontinuities at rest. Offline
gyro correction removes most steady no-GPS drift but does not remove the
GPS-on wobble; these are not live calibration or successful driving results.

## Gyro-confidence and GPS-heading policy comparison

Experimental startup controls in both perception modes:

| Argument | Default | Effect |
| --- | --- | --- |
| `gnss_heading_gain` | `1.0` | Scale [0,1] on GPS yaw/yaw-rate Kalman gain rows |
| `yaw_rate_variance` | `0.0004` | Gyro rate measurement variance, (rad/s)²; smaller gives measured rate more confidence |

For a stationary desktop diagnostic, append `auto_go:=false` and, for example,
`gnss_heading_gain:=0.25` to the GPS launch command. This is a constant gain;
there is no automatic stationary/moving switch. Zero GPS angular gain still
allows real gyro turns but prevents GPS-aided starting-heading recovery.
The defaults are unchanged. These flags do not calibrate the live gyro;
trusting its raw biased reading more does not remove the bias.
[Implementation and limitations](../lhr_state_estimation/README.md#experimental-heading-controls).

To reproduce the controlled comparison after sourcing ROS/workspace:

```bash
python3 src/lhr_gazebo/scripts/compare_heading_policies.py \
  data/heading-attribution/gps08-live data/heading-policy-reproduce \
  --moving-capture data/gnss-study/standard-50hz --plot
```

The named input directories refer to retained local recordings, not files
shipped in git. Capture equivalents with the earlier recorder commands if
those recordings are unavailable. Calibration uses the predeclared 2–5 s
stationary interval; all cases use the same 5 s restart, initial covariance,
input sequence and GPS consumption timestamps. Compare default gyro variance,
variance measured from calibration, and measured variance with GPS angular
gain 0.25/0.1/0. Each case tests initial heading offsets 0/−10/+10 degrees.
The stress offsets are assumptions, not a hardware heading specification.

Six ideal planar synthetic controls cover straight acceleration and a slow
turn, each at seeds 3/7/11. They use canonical noise/bias scales, 100 Hz IMUs,
50 Hz GPS and a 50 ms delivery delay. They are model-level tests, separate from
Gazebo. Recovery means the first two continuous seconds with heading error
within 2 degrees after motion begins at 7 s. This is a diagnostic criterion,
not the autonomous gate. Recorded moving scoring ends at 12.5 s, before the
retained capture's first cone contact. Replays do not simulate how a different
estimate would alter closed-loop steering.

See [results and source/data fingerprints](../../../testing/2026-11-08-native-imu/README.md#gyro-confidence-and-gps-heading-gain-comparison).
A smaller constant angular gain improved stationary wobble but slowed recovery
from uncertain initial heading; no new default or driving-success claim follows.

## Calibrated gyro-heading visual profile

Build once after updating the workspace, then launch the experimental recipe:

```bash
./scripts/build.sh
./scripts/run_gyro_heading_demo.sh
```

It opens Gazebo, RViz and Foxglove on autocross seed 3, with native noisy IMU,
wheel fusion off, simulated GPS on, `calibrate_gyro:=true` and
`gnss_heading_gain:=0.0`. The GUI environment includes the existing xcb/font
workaround. Trailing launch arguments override defaults:

```bash
# Inspect the calibrated stationary scene without automatic driving.
./scripts/run_gyro_heading_demo.sh auto_go:=false
# Use the exact recipe headlessly.
./scripts/run_gyro_heading_demo.sh gui:=false rviz:=false foxglove:=false
```

The vehicle waits through settling and a sensor-verified calibration window.
Estimated odometry/TF and the RViz map appear only after calibration succeeds;
watch for `Gyro calibration ready` in the terminal. Normal READY hold and
automatic driving follow. Gazebo still shows the physical vehicle throughout.
A calibration failure leaves odometry unpublished; inspect the reported
reason and restart after addressing it. Do not interpret the initial missing
map/TF as an estimator crash. Default metrics output is
`data/gyro-heading-visual.csv`.

The gyro-z offset is estimated from quiet IMU samples plus independent raw
LiDAR scene stability, not truth or GPS. The Gazebo start-pose seed remains
one-time truth initialization; absolute starting heading is not learned from
the gyro. Real gyro turns are preserved; GPS cannot update yaw or yaw rate in
this recipe. GPS-induced translation jitter, acceleration bias and false
stationary speed may remain. There is no zero-velocity correction, adaptive
heading gain or retrospective cone-map repair.

All calibration parameters and provisional validation limits are listed in
[the estimator guide](../lhr_state_estimation/README.md#live-startup-gyro-calibration)
and exposed as typed launch arguments in both perception modes. Reliable
buffered raw LiDAR delivery is the Gazebo default; select
`gyro_calibration_lidar_qos:=sensor_data` only for a best-effort source.
To inspect late-joining calibration status:

```bash
ros2 topic echo /lhr/state_estimation/gyro_calibration --once \
  --qos-durability transient_local
```

To record and independently verify the profile:

```bash
export ROS_DOMAIN_ID=125 GZ_PARTITION=gyro-heading-reproduce
python3 src/lhr_gazebo/scripts/record_ekf_stop.py data/gyro-heading-recording \
  --perception-study --gnss --calibrate-gyro --gnss-heading-gain 0 \
  --stop-sim-time 30
python3 src/lhr_gazebo/scripts/analyze_gyro_heading_demo.py \
  data/gyro-heading-recording data/gyro-heading-analysis
```

Add `--stationary` for a no-auto-go control. The analyzer verifies calibration
against independent physics, checks that no odometry preceded readiness,
audits actual GPS angular changes, and reports heading/position error and
native contacts. A stationary run may have no lap metrics CSV because it
never entered DRIVING; its sensor/pose analysis remains available.

The [saved live tests](../../../testing/2026-11-08-native-imu/README.md#live-calibrated-gyro-heading-and-gps-position-visual-profile)
include successful calibration, a headless execution of this exact wrapper,
and a short moving run. The moving run ended on ODOM_STALE; it is not a clean
full lap or gate pass. GUI rendering itself remains for the user to verify.
