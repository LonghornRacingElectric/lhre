# lhr_lidar_sim

A simulated Livox Mid-360 using the **official Livox beam table**, ray
casting against cones and ground, and seeded noise and dropout. Publishes
`sensor_msgs/PointCloud2` on `/lhr/lidar/points`.

Livox's complete simulator plugin targets Gazebo Classic 9 and ROS 1.
Our workspace uses Gazebo Harmonic and ROS 2 Jazzy, so we import its scan
data into the existing headless caster rather than depend on that plugin.
A regular Gazebo `gpu_lidar` uses a uniform angular grid and does not
replay this table. This package currently runs in the kinematic demo;
the Gazebo demo still uses its own sensor.

## Layout

| File | Role |
| --- | --- |
| `mid360.py` | Beam pattern and range model. No ROS. |
| `scene.py` | Ray casting against ground and cones. No ROS. |
| `sensor.py` | Mount pose and the per-frame pipeline. No ROS. |
| `lidar_sim_node.py` | The ROS wrapper. |

Only the ROS wrapper imports ROS. The numerical modules also need NumPy
and the shared `lhr_vehicle` and `lhr_trackgen` packages.

## Imported scan pattern

The default `elevation_profile=livox` replays all 800,000 rows of
[Livox's Mid-360 scan table](https://github.com/Livox-SDK/livox_laser_simulation/blob/1cce1073633a062b92e30243a4c2920e45551bb5/scan_mode/mid360.csv).
The source revision is `1cce1073633a062b92e30243a4c2920e45551bb5`.
The bundled `patterns/mid360.csv.gz` is a lossless gzip copy; the original
CSV SHA-256 is
`aa1fc08b6a4400608dbd6ee832b7ea3a9c3c37197e734f60f58fe5abf762269a`.
The accompanying `patterns/LICENSE` preserves Livox's MIT license.
Both files are installed with the Python package; runtime needs no download.

Conversion matches Livox's plugin: azimuth is the second column in
degrees and elevation is `90 - zenith` from the third column. The first
column increments once per sample despite its `Time/s` heading. We replay
rows at `point_rate_hz`, using absolute simulation time so splitting a
scan into frames does not reset the sequence. At the default 200 kHz,
the table lasts four seconds, then wraps. This finite replay repeats;
it is not a claim that the physical sensor repeats every four seconds.
Changing the point rate changes playback speed, not angular resolution.

`rosette` and `uniform` retain the old synthetic sweeps for comparison.
The table spans -7.2123 to +52.164 degrees, slightly beyond the nominal
vertical limits; those vendor angles are preserved without clipping.
The synthetic profiles are approximations, not device calibration. Their
elevation limits and sweep frequency do not modify the imported `livox`
table.

## Physical limits

The nominal [Mid-360 specifications](https://www.livoxtech.com/mid-360/specs)
are 360 degree horizontal coverage, -7 to +52 degree vertical coverage,
and 200,000 points/s (20,000 beams per 10 Hz frame). Importing the vendor
pattern improves beam direction and density; it does not make every
aspect of the simulated returns match hardware.

The caster uses ideal cone surfaces and a flat ground plane. Range
noise is a constant Gaussian along each ray, and dropout is a constant
fraction. The 40 m maximum corresponds to the datasheet's 10% reflectivity
condition; the model does not reproduce material or lighting dependence.
Mount studies and detector performance still need validation against
recordings from the car. Previous return-count estimates based on the
synthetic sweeps are not measurements of a Mid-360.

## Running it

As a node, inside the demo stack:

```bash
ros2 launch lhr_demo mvs_demo.launch.py lidar:=true mount_pitch_rad:=0.25
ros2 launch lhr_demo mvs_demo.launch.py lidar:=true record:=true
```

`lidar:=true` keeps simplified detections for driving while publishing the
cloud for inspection. LiDAR-only driving is not wired into this launch yet;
that needs a separately tested planner and controller integration. See
[demo launch](../lhr_demo/README.md).

Off by default so the existing kinematic run has no extra sensor load.
This preview does not change its `lhr_sensor_sim` driving input.

Standalone, with no ROS at all:

```python
from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose, points_on_cone
import numpy as np

sensor = Mid360Sensor(config=Mid360Config(),
                      mount=MountPose(pitch_rad=0.25), seed=1)
points, on_cone = sensor.frame_labeled(
    t=0.0, vehicle_x=0.0, vehicle_y=0.0, vehicle_yaw=0.0,
    cones_xy=np.array([[8.0, 0.0]]))
print(points.shape, on_cone.sum())
print(points_on_cone(sensor, 0.0, distance_m=10.0, frames=10))
```

The table is decoded once on first use and cached for subsequent frames.
The caster sorts beam azimuths once per cone-size group, then intersects
only the rays inside each cone's silhouette. Square bases use a larger
bounding circle so their corners are retained. This avoids checking every
beam against every base while preserving the original surface hits.

A local benchmark of the recorded 190-cone autocross scene, with 20,000
beams per frame, took about 34 ms per frame after this culling change
versus 717 ms before it. These are warmed numerical timings on one machine;
live publishing rate also depends on startup, ROS scheduling and recording.

## Moving the sensor without restarting

All six mount degrees of freedom accept a new value while the node runs,
so the mount study is a slider and a look rather than a rebuild per
guess:

```bash
ros2 param set /lidar_sim mount_pitch_rad 0.25
ros2 param set /lidar_sim mount_z_m 1.20
```

The next frame is cast from the new pose and the `lidar` frame moves
with it. Foxglove can change the live node parameters when
`foxglove:=true`; no bag or relaunch is needed.

Position defaults come from `vehicle.yaml` so that file stays the source
of truth. A measured answer gets written back there, not left in a
launch argument. Non-numeric and non-finite values are refused, and
because a parameter callback runs before the value is stored, a refused
one leaves the node on the pose it already had.

Changes are recorded: the transform rides `/tf`, which every bag
records, so a recording shows where the sensor was for every frame in
it even if someone moved it mid-run.

## Frames

The node publishes the `base_link -> lidar` transform from its own mount
pose, and that is not optional bookkeeping. Clouds are stamped in the
`lidar` frame, and the kinematic stack's entire tf tree is one
transform, `map -> base_link`. Without this one, nothing can place the
cloud: Foxglove, RViz and every tf2 consumer simply draw nothing, with
no error to explain why.

It is published here rather than from a URDF because this node already
owns the mount pose, and a second copy would drift from it. A side
benefit is that the mount pitch becomes visible in the viewer, which is
the thing the mount study is arguing about.

Each cloud and its mount transform use the timestamp of the odometry pose
used to cast the scan, so detection can look up the same vehicle pose rather
than a later timer pose. Scan-pattern time still follows the simulator clock.

It goes on `/tf`, not `/tf_static`, even though the mount holds still
for the length of any one run. `StaticTransformBroadcaster` adds a child
frame the first time it sees one and **silently ignores every send
afterwards**, republishing the first pose it was given:

```python
for t_in in transform:
    if t_in.child_frame_id not in self._child_frame_ids:
        self._child_frame_ids.add(t_in.child_frame_id)
        self.net_message.transforms.append(t_in)
self.pub_tf.publish(self.net_message)
```

With the mount on a slider that is a trap, and a quiet one: the node
logs that the mount moved, the cast uses the new pose, and the frame
every viewer draws stays where it started. A transform that can change
is not static.

```
map -> base_link -> lidar     (1.80, 0.00, 0.62), pitch as parameterised
```

### In the `lidar` frame, `z` is not height

On a pitched mount the sensor frame is tilted, so a point's `z` field is
not its height above the ground. At `mount_pitch_rad:=0.25` a flat
parking lot spans `z` from -4.7 m to +8.4 m in the cloud's own
coordinates, while in `base_link` the same points sit flat within a
couple of centimetres.

This matters when a viewer colours by the raw `z` field. It reads as height
at zero pitch and as a gradient across flat ground once the mount is tilted.
The cloud is correct either way; transform into `base_link` to check height.

## Parameters

| Parameter | Default | Notes |
| --- | --- | --- |
| `mount_x_m` | `vehicle.yaml` | Mount position in `base_link`. Live |
| `mount_y_m` | `vehicle.yaml` | Live |
| `mount_z_m` | `vehicle.yaml` | Live |
| `mount_roll_rad` | `0.0` | Live |
| `mount_pitch_rad` | `0.0` | Positive is nose down. Live |
| `mount_yaw_rad` | `0.0` | Live |
| `seed` | `1` | Node-local stream, so a seed repeats |
| `frame_rate_hz` | `10.0` | |
| `point_rate_hz` | `200000.0` | Datasheet |
| `max_range_m` | `40.0` | Datasheet, at 10% reflectivity |
| `range_noise_std_m` | `0.02` | Applied along the beam, not per axis |
| `dropout_rate` | `0.0` | Returns lost outright |
| `elevation_profile` | `livox` | Imported vendor table; `rosette`/`uniform` are legacy approximations |
| `return_profile` | `baseline` | Optional `acceptance_overcast` small-cone thinning |
| `motion_distortion` | `false` | Ten acquisition-pose groups per frame |
| `clutter_profile` | `none` | Optional `trackside` boxes |

The preview launch forwards the seed, mount pitch, beam profile, return
profile, motion distortion, and clutter profile with explicit types; the six mount
parameters can be changed live through the node. Other sensor settings
are read at startup. `mount_pitch_rad:=0` works as
well as `0.0`. See [lhr_demo](../lhr_demo/README.md).

The mount **position** comes from
[`lhr_vehicle`](../lhr_vehicle/README.md), because vehicle numbers live
in one file. The mount **orientation** does not, because it is the
answer the mount study owes and guessing it into `vehicle.yaml` would
make a placeholder look like a measurement.

## Gotchas

- `points_on_cone`'s `distance_m` is measured **from the sensor**, not
  from `base_link`. The mount sits 1.8 m ahead of the rear axle, and
  measuring from the axle shortens every range by that much, which near
  the field of view floor is the difference between seeing a cone and
  seeing nothing.
- Returns are labelled by the caster, not classified by position. At a
  cone's base a cone return and a ground return sit millimetres apart,
  so counting by position quietly counts ground as cone. `cast` returns
  an `on_cone` mask because it already knows.
- A ray aimed exactly at a cone's apex is the tangency case: the
  discriminant is algebraically zero and lands either side of it on
  rounding, measured at 1e-16 relative. `scene.py` clamps a small
  negative discriminant to zero, otherwise the nearest point of a cone
  is the one direction that can miss it, platform-dependently.
- **A crashed lidar node does not stop the car.** Preview mode still uses
  simplified detections for driving. Check the log and confirm
  `/lhr/lidar/points` has messages in the bag before treating a sensor
  preview as valid.

## Not done yet

- Small and large cones have idealized opaque surfaces, with no material
  reflectivity model. Baseline `dropout_rate` is flat; the optional overcast
  profile covers only measured small-cone ranges and conditions.
- Motion within a frame is off by default. `motion_distortion:=true` uses
  ten pose groups per scan, not a measured per-beam time and deskew model.

## Hardware comparison

The [Mid-360 acceptance results on main](https://github.com/LonghornRacingElectric/lhre/blob/52fb09e6a71e788323171ac86d29c1fae98e08c3/autonomy/testing/2026-10-04-mid360-acceptance/README.md)
include measured cone hit counts and frame occupancy versus range, lighting,
and pitch. The imported vendor beam table does not reproduce those material
and lighting losses. Use those results and the linked recordings to validate
future return-model changes; do not equate the nominal 40 m range with reliable
small-cone detection at that distance.

## Competition cone geometry

The caster reads nominal dimensions from
[lhr_trackgen](../lhr_trackgen/README.md). It intersects a continuous ideal
cone and a 10 mm square base slab; the nearest surface wins over ground and
other cones. The scene supports both small and large cones. An Nx2 cone
array means small cones; Nx4 arrays contain x, y, height, square-base width
in metres. Geometry is idealized; stripe reflectivity is not modeled.

## Acceptance comparison

`compare_acceptance` bundles the published small-cone results from main
commit `52fb09e6a71e788323171ac86d29c1fae98e08c3`, keeping indoor and outdoor
runs separate. It reproduces each measured range and sensor height with a
level sensor and reports simulated versus observed mean points per frame
and percentage of frames with returns. It also reports availability of
three points across five frames. It does not silently fit a dropout curve.

```bash
ros2 run lhr_lidar_sim compare_acceptance --run small_10p16m_level \
  --run small_out_10p16m_level_run2 --output data/acceptance-comparison.json
```

Use `--results PATH` for another results table or `--raw-dir PATH` for a
folder containing downloaded acceptance NPZs. Captures use `xyz[N,3]` in
metres, `t[N]` in seconds and `imu[N,6]` (gyro followed by acceleration),
matching the acceptance tools. The evaluator levels stationary captures
using mean IMU gravity and counts returns in a 35 cm ROI around the
published cone location. It compares the 5 cm and 15 cm height cutoffs and
includes missed frames in availability. ROI availability is not detector
recall; clutter in the ROI can inflate the count. Moving recordings need
stamped odometry and a replay through the production detector instead.

Reports explicitly set `calibrated: false`: scan phase, exact molded cone
shape, mount pitch, ambient-light response and real recordings remain
validation inputs. Raw recordings stay outside Git. The published results
are measurements, not a substitute for the ROS bags needed to tune false
positives, range errors and production detector recall.

## Recorded overcast return profile

`return_profile=acceptance_overcast` applies a seeded, range-dependent
survival probability **only to small-cone returns**. Ground, large cones
and other surfaces retain the baseline model. Use it for studies of
small-cone return scarcity under the October 4 overcast conditions:

```bash
ros2 launch lhr_demo mvs_demo.launch.py lidar:=true \
  return_profile:=acceptance_overcast
```

The derived table is packaged with the simulator, so running this profile
needs no recordings. Its training entries retain capture SHA-256 values.
The fit uses measured stationary mount angles from mean IMU gravity and
published cone ranges/heights, then divides recorded ROI return density
by baseline simulated density. It compensates the aggregate mismatch;
it does not separately identify ambient-light, shape and scan-phase effects.

Repeat runs 3 and 4 at 10.3 m are held out. The fitted expected point counts
were about 1.00 versus 0.89 measured in run 3, and 0.87 versus 1.29 in run 4.
Run 4 had fading light: that transfer error is retained in the profile,
not removed by training on the held-out recording.

Reproduce the fit from local raw recordings:

```bash
ros2 run lhr_lidar_sim compare_acceptance \
  --raw-dir data/2026-10-04-mid360-acceptance/raw \
  --fit-overcast data/acceptance-overcast.json \
  --output data/acceptance-calibrated-comparison.json
```

To check the packaged fit, pass `--return-profile acceptance_overcast`
without `--fit-overcast`. When raw captures are supplied, the comparison
uses their gravity-derived mount angles; otherwise it assumes a level
sensor. The default `return_profile=baseline` remains the unmodified
return-density model. Both modes still apply configured range noise.

The fit covers approximately 2.7–17.9 m. Interpolation holds endpoint values
outside that range, which is unvalidated extrapolation. It is an empirical
stationary small-cone profile, not validation of intensity, arbitrary
materials, direct sunlight, moving scans or temporal dropout correlation.
The report's `calibrated: false` flag means the complete sensor model remains
unvalidated even when a component is fitted. Evaluation through the
production detector is a separate integration step.

## Motion and clutter studies

MVS launch accepts `motion_distortion:=true` and `clutter_profile:=trackside`.
Defaults remain false and `none` for comparisons with prior results.

Motion scans cover the 100 ms ending at the cloud stamp. Ten chronological
beam groups use interpolated recorded odometry, with yaw unwrapped across
±π. Startup waits for a complete pose history. Points retain the sensor
coordinates at acquisition time; a detector that uses only the cloud end
stamp cannot deskew them. Groups approximate motion at 10 ms resolution;
this is not exact per-point hardware timing. Clouds still contain XYZ only.

Trackside clutter consists of reproducible 0.6 × 0.6 × 0.45 m solid boxes,
placed 0.8 m outward from every eighth boundary cone. These generic objects
produce actual surface returns and occlude farther cones and ground.
Their markers appear on `/lhr/scene/clutter`; enable that topic in the
Foxglove 3D panel to see them. Their positions never enter
perception as cone labels. Material reflectivity is not modeled. Combine
with `return_profile:=acceptance_overcast` to test the existing recording-
informed cone dropout model; it is not a general weather simulation.

Terrain remains flat and the kinematic car stays level. Uneven ground, vehicle
roll/pitch, sensor vibration, sunlight interference and calibrated material
intensity remain future work. These scenarios test robustness without
claiming the unavailable car has been physically validated.


## Integration verification (2026-10-18)

The isolated kinematic preview built all 13 workspace packages and passed
210 ROS tests plus the strict documentation build. Culling was compared
exactly against the original caster on three recorded-scene orientations
and ten seeded random scenes; unit tests also disable culling to check
wrapped azimuths, near cones, square corners and vertical beams.

Bounded live runs recorded the following clouds:

| Odometry mode | Clouds | Arrival rate | Clouds with matching mount TF and physical odometry stamp |
| --- | ---: | ---: | ---: |
| Truth | 276 | 10.05 Hz | 276 |
| EKF, launch defaults | 325 | 10.03 Hz | 325 |
| EKF, bias/scale errors and live mount changes | 424 | 10.03 Hz | 424 |

Median cloud-stamp intervals were about 0.1 s. Startup table decoding
caused maximum intervals of 0.44–0.60 s; these measurements do not promise
hard real-time scheduling. A live change to pitch 0.25 rad and height
0.70 m appeared in the recorded TF, and transformed ground returns stayed
near zero height. In EKF mode all cloud stamps matched truth odometry,
not the independently stamped estimate.

These runs verified sensor output and vehicle movement. They ended by
interruption while the mission was `DRIVING`, with nonzero commands;
finish and stopping were not validated. One stress run with a shortened
0.5 s ready hold produced a 410 m/s estimator spike. The kinematic plant
uses fixed integration steps with wall-clock stamps, while the inertial
simulator differentiates those stamps, making delayed callbacks a timing
risk. That spike did not recur with default launch settings. Existing lap
metrics also signaled completion without a `FINISHED` mission in the
stress run and a no-LiDAR control. Treat those metrics as diagnostics,
not the approved MVP gate.

The optional ten-pose motion approximation was slower than the default
snapshot caster on this machine (about 0.21 s per warmed frame). It is a
study mode and has not demonstrated 10 Hz live output. Foxglove application
rendering and hardware agreement remain unverified.
