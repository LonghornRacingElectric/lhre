# lhr_vehicle

One file, `config/vehicle.yaml`, holds Orion's physical parameters
(wheelbase, track, wheel radius, masses, steering limits, the LiDAR mount)
and every consumer reads it from there:

- `lhr_control` (pure pursuit) and `lhr_sim_kinematic` take their
  `wheelbase` / `max_steer` parameter *defaults* from it. Launch-file
  overrides still work; the file only replaces the hardcoded fallbacks.
- `lhr_gazebo/joint_cmd_adapter` uses it for the Ackermann and
  differential-speed math.
- `lhr_perception/lidar_cone_detector` uses the body dimensions for the
  self-exclusion box. It takes its mount from TF by default and uses the
  LiDAR position for the sensor→`base_link` transform only with
  `sensor_transform_source:=vehicle_config`, which the Gazebo launch
  passes. `evaluate_recordings` uses the mount x/y and stamps the file's
  hash on its results.
- `lhr_lidar_sim` takes its default mount position from it.
- `lhr_metrics` builds the whole-vehicle footprint envelope from the body
  and wheel dimensions, and stamps the file's hash on every row.
- `lhr_safety` takes its wheelbase and speed-limit defaults from it.
- `lhr_sensor_sim/inertial_sim` takes its wheel radius and track defaults
  from it, and `lhr_state_estimation/ekf_node` its wheel radius.
- `lhr_sim_bobsim` refuses to start when BobSim's wheelbase or wheel radius
  disagrees with it, and records its hash in the plant provenance.
- `lhr_gazebo` publishes the stereo camera transforms from it, and
  `generate_world.py` uses the body and wheel extents to stage the car's
  nose behind the acceleration start line.
- The Gazebo models `lhr_gazebo/models/fsae_vehicle/model.sdf` and
  `model_stereo.sdf` are
  **generated** from it (see below); wheel poses, joint limits, masses and
  sensor poses all derive from the same numbers. The steering joints get
  the Ackermann *inner* angle at full lock, since that is what they see.

Why: before this package the wheelbase and the steering limit were repeated
in four places with four different steering values (0.45, 0.55, 0.69 and
0.7 rad), and none of the numbers had a stated origin.

## Frame

`base_link` is the rear-axle center at ground level, x forward, y left,
z up. Sensor positions are given in that frame.

The LiDAR mount remains main's assumed z=0.55 m pending Gazebo validation.
The CAD nose clearance check suggests a higher mount; that geometry alone
does not validate a new detector configuration.

## Fingerprinting the file

`vehicle_sha256()` hashes the file's bytes. Anything derived from these
numbers stamps it, so a stored result says which car description produced
it. A commit id does not cover that, because the file is often edited
without being committed.

```python
from lhr_vehicle import vehicle_sha256

vehicle_sha256()   # 'd364f17c...', 64 hex characters
```

Same formula as `simulation/tools/provenance.py`, so a sim run and a
BobDyn study are comparable on that one field. `lhr_metrics` writes it on
every row.

## Provenance

Each value carries a tag in the YAML: `BobSim` for numbers derived from
VMOD's [BobDyn/BobSim](https://github.com/BobDyn/BobSim) `vehicle.yml` for
Orion, `ASSUMED` for simulation placeholders that still need measuring on
the car. When you measure one, replace the number and drop the tag.

## Usage

```python
from lhr_vehicle import load_vehicle

veh = load_vehicle()
veh.wheelbase_m, veh.track_m, veh.max_steer_rad, veh.lidar_position_m
```

`sensors.lidar.range_noise_std_m` sets Gazebo's along-beam Gaussian range
noise standard deviation in metres, exposed as `veh.lidar_range_noise_std_m`.
The provisional default is 0.02 m, based on the 10/4 Mid-360 acceptance noise
scale and matching the kinematic Mid-360 simulator. Values must be finite and
non-negative; zero disables range noise. Regenerate the Gazebo model after
changing it. See [the noise model](../lhr_gazebo/README.md#range-noise) for
provenance and limits.

`load_vehicle()` finds the installed copy through the ament index, or the
source-tree copy when imported straight from the checkout. The model
generator always passes the source-tree path explicitly so an installed
copy can never be stale relative to the YAML being edited.

## Gazebo actuation

Gazebo's wheel speed controllers apply bounded joint torques rather than
setting joint velocities directly. `simulation.gazebo_drive` exposes
`velocity_p_gain_nm_s_rad` (10 N·m per rad/s of speed error) and
`max_wheel_torque_nm` (100 N·m symmetric drive/brake clamp), loaded as
`veh.gazebo_drive`. These positive, finite values are **ASSUMED simulator
tuning**, not measured Orion motor limits. Integral and derivative gains are
zero. Regenerate both SDFs after editing them. The force response avoids the
acceleration pulses that the 100 Hz physics IMU missed under direct velocity
commands; it introduces speed-tracking lag and does not model motor electrical
dynamics or calibrated tire slip. The kinematic and BobSim plants do not use
these Gazebo-specific settings.

## Native Gazebo IMU

`sensors.imu` configures independent errors on all three gyro and accelerometer
axes in both generated models. [Livox identifies the Mid-360 IMU as
ICM40609](https://www.livoxtech.com/mid-360/specs). The model uses
[TDK DS-000330 revision 1.2, tables 1 and 2](https://product.tdk.com/system/files/dam/doc/product/sensor/mortion-inertial/imu/data_sheet/ds-000330_icm-40609-d_v1.2.pdf)
for the ICM-40609-D; Livox does not identify the chip suffix or firmware
filter configuration, so this is a component-datasheet approximation.

| Quantity | Datasheet | Model |
| --- | --- | --- |
| Gyro noise density | 4.5 mdps/√Hz | 0.00007853981634 rad/s/√Hz |
| Accel noise density | 100 µg/√Hz | 0.000980665 m/s²/√Hz |
| Initial gyro offset tolerance | ±1 °/s, board level at 25 °C | Zero-mean startup bias, σ = 1 °/s / 3 = 0.00581776417331 rad/s |
| Initial accel offset tolerance | ±40 mg, board level | Zero-mean startup bias, σ = 40 mg / 3 = 0.130755333333 m/s² |

The **one-sided** density conversion is `sample_sigma = density * sqrt(B)`.
The Gazebo sensor runs at **1000 Hz**, matching the default world's 1 ms
physics step so short impacts are captured. `B = 500 Hz` is an assumed
one-sided Nyquist bandwidth, not Livox's measured bandwidth or output cadence.
Per-sample sigma is sqrt(10) times the former 100 Hz / 50 Hz-bandwidth model;
`sigma² / sample_rate`, and hence the integrated white-noise intensity, stays
the same. Datasheet densities and startup bias distributions are unchanged.
This is a simulator acquisition setting, not a claim that the physical Mid-360
publishes at 1000 Hz. The EKF processes every stamped sample and publishes
odometry separately at 50 Hz; no raw IMU decimation is performed.
Use 9.80665 m/s² for unit conversion. The loader rejects non-finite/negative
values, nonpositive rates and bandwidths above Nyquist.
This bandwidth sets the noise amplitude; it does not implement a temporal
filter on Gazebo's acceleration signal.

Mapping the datasheet's typical ±offset scales to ±3σ is an **assumed
distribution**, not a manufacturer probability or a hard bound. Gazebo draws
one independent constant bias per axis at sensor startup, plus white noise
on every sample. Dynamic bias drift is left zero: temperature coefficients
do not specify a stochastic drift process. Temperature, scale-factor errors,
cross-axis coupling and clipping are not modeled. The donated unit's
[acceptance measurements](../../../testing/2026-10-04-mid360-acceptance/README.md)
show larger temperature-dependent gyro offsets; these defaults do not claim
to calibrate that unit. The mount and sensor cadence remain unchanged.

See [Gazebo controls and verification](../lhr_gazebo/README.md#native-imu-noise-and-bias)
for the noise-free comparison and additional signed bias fault.

## Regenerating the Gazebo model

After editing `vehicle.yaml`:

```bash
cd ros2
python3 src/lhr_gazebo/scripts/generate_vehicle_model.py
./scripts/build.sh
```

Commit the regenerated `model.sdf` and `model_stereo.sdf` alongside the YAML
change. The default model omits cameras; the stereo variant adds the rig.
Both are checked in, like the generated world files.
`generate_vehicle_model.py --check` reports whether both agree with the YAML
and templates, and `lhr_gazebo`'s tests fail when they don't, so CI catches a
forgotten regenerate.

## Seeing the car

`vehicle_viz` publishes the car on `/lhr/vehicle/body`, in `base_link`. It
exists because a 3D viewer otherwise shows a grid, a line and a cloud,
with nothing to say where the car is or which way it points.

```bash
ros2 run lhr_vehicle vehicle_viz
```

By default it draws Orion's actual CAD, as a `MESH_RESOURCE` marker
pointing at [`meshes/orion.stl`](meshes/README.md). The mesh is in metres
and its origin is the front axle, so the marker's whole transform is a
translation of one wheelbase along x, read from this file rather than
hardcoded.

In Foxglove, set **3D panel → Scene → Mesh up-axis → Z-up** (already
set in the committed `lhr_sim` layout). STL has no up-axis metadata;
loading this Z-up CAD as Y-up rolls the car onto its side. Existing
layouts need that setting changed or the updated layout imported.

```bash
ros2 run lhr_vehicle vehicle_viz --ros-args -p use_mesh:=false
```

`use_mesh:=false` falls back to the older primitives, a chassis box and
four cylinders. That fallback is kept deliberately: a mesh marker names an
asset the viewer has to **fetch**, which works against a live
`foxglove_bridge` but not when replaying a bag, because a bag has no asset
server. If a recorded run has to open as a visible car, record it with
`use_mesh:=false`.

Serving the mesh needs no bridge configuration. `foxglove_bridge`'s
`asset_uri_allowlist` already defaults to a pattern that allows
`package://<pkg>/<path>/<name>.stl`.

The demo launch starts the node, and `mvs_demo.launch.py` records the
topic. The markers are latched and published once rather than on a timer:
the car's shape does not change, and a marker array per tick would bloat
every bag for no information.

The primitives are still worth understanding, because `use_mesh:=false`
draws them and their two halves differ in provenance. The wheels and axles
are real: wheelbase, track and wheel radius all come from BobDyn (see
[Provenance](#provenance)). The chassis box is not, since
`body_length_m`, `body_width_m` and `body_height_m` are `ASSUMED`
placeholders. That gap is exactly what the mesh closes, and
[`meshes/README.md`](meshes/README.md) records where it came from and the
one body in it that is reconstructed rather than extracted.

## Stereo camera

The car uses one ZED 2i with the 2.1 mm wide lens. `sensors.stereo_camera`
contains its 0.12 m baseline, rig-center pose in `base_link`, and simulated
image size/rate/FOV. `load_vehicle().stereo_camera` exposes these values and
pixel intrinsics to the model generator and camera transforms. Invalid or
missing camera configuration fails explicitly; rebuild installed packages
after updating the YAML.

The initial mount is assumed. Images use an ideal rectified pinhole with
zero distortion and square pixels, rather than a physical camera calibration.
At 1280x720, the 110-degree horizontal FOV implies about 77.6 degrees vertical;
manufacturer FOV values do not supply exact rectified intrinsics. Measure the
mount and obtain device calibration before matching the physical unit.
See the [stereo design](../../../docs/plans/zed2i-stereo.md) for scope.

## GNSS experiment configuration

`sensors.gnss`, exposed as `load_vehicle().gnss`, holds the antenna mount and
simulator rate, per-axis horizontal/vertical sigma and delivery latency. Values
must be finite; rate and sigmas must be positive, latency non-negative.
The default antenna `[0, 0, 0]` is a virtual `base_link` antenna, not a surveyed
physical mount. GNSS is generated by a ROS sensor simulator, not an SDF sensor;
this section does not change the generated Gazebo models.

The repository's LocusLock telemetry notes list 10/50/100 Hz support and a
50 Hz experiment. BEVO parses full-precision `$PPOS` position, horizontal
uncertainty, standard/RTK fix source and UTC time. Its checked-in standard-fix
example reports `sigma_h=0.792 m`; that is a test fixture, not a receiver
accuracy guarantee or confirmation of independent per-axis error. Accordingly,
50 Hz, 0.8 m independent horizontal sigma, 1.2 m vertical sigma and 50 ms
latency are explicitly assumed simulation settings. The optional 0.05 m
horizontal comparison is also assumed, not a measured RTK specification.
See [receiver notes and results](../../../testing/2026-11-08-native-imu/README.md#periodic-gnss-experiment).
