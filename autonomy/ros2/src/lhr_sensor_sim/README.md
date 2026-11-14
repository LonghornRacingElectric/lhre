# lhr_sensor_sim

Takes the ground-truth cones on `/lhr/track/cones`, keeps the ones inside
the vehicle's field of view and range, and republishes them on
`/lhr/sensor/cones_detected`. It is a visibility filter, not a sensor model.

Why it exists: it lets planning and control run without a point cloud or a
renderer, so the kinematic stack works headless, on any OS, and in CI.

What it costs: it hands out cone positions directly, so `perception:=sim`
never exercises `lhr_perception/lidar_cone_detector`. `perception:=lidar`
swaps it for [`lhr_lidar_sim`](../lhr_lidar_sim/README.md) and the real
detector. This package is a shortcut with a name, kept because the
`perception:=sim` baseline needs a cone source with no point cloud.

## Parameters

| Parameter | Default | What it does |
| --- | --- | --- |
| `fov_deg` | 200.0 | Angular width of the detection wedge |
| `max_range_m` | 20.0 | Cones beyond this are not detected |
| `min_range_m` | 0.5 | Cones closer than this are not detected |
| `detection_hz` | 10.0 | Detection tick rate |
| `noise_std_m` | 0.0 | Gaussian position noise, metres std dev |
| `false_negative_rate` | 0.0 | Chance a visible cone is missed on a tick |
| `seed` | 1 | Seeds this node's random stream |

## Accumulation

A cone is measured once. After it enters `_accumulated` it is never
re-measured and never forgotten, so position noise is applied a single
time and then persists for the whole run. That matches a mapping sensor
more than a tracking one, and it means the detected set only ever grows.

## Determinism

`seed` seeds a node-local `random.Random` rather than the process-global
`random`. The global one is shared with every other node in the process,
so draws interleave and no run repeats.

What that does guarantee: a given seed produces the same noise and the
same missed cones for a given sequence of draws.

What it does not: the draw *order* depends on how many cones enter the
field of view on each tick, which depends on when odom arrives relative
to the detection timer. The kinematic stack is stable enough to repeat.
Gazebo is not, and never will be. If run-to-run equality turns out to
need more than this, derive each cone's noise from its identity
(`seed`, `ns`, `id`) instead of from a sequential stream, which is
deterministic whatever the order.

One surprise worth knowing: the false-negative draw happens per tick, not
per cone, so at 10 Hz a cone rejected once is usually detected within a
second. The parameter models a transient miss, not a cone the stack never
sees.

Cone triangle vertices and stripe colors are preserved in detections and
visualization. The simplified sensor copies each scene marker before adding
position noise, so it cannot alter the ground-truth scene positions.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

## inertial_sim

The inertial node, started by `estimator:=ekf` when synthetic IMU or wheel output is needed. It degrades
`/lhr/vehicle/odom_truth` into `/lhr/imu/data` and four wheel speeds on
`/lhr/sensor/wheel_speeds`, the inputs the
[EKF](../lhr_state_estimation/README.md) fuses. Feeding the filter raw
truth would prove nothing, so the error is the point.

Speed and yaw rate come from differencing the truth pose rather than
reading its twist, so the result does not depend on which frame a plant
reports twist in. Wheel radius and track default from
[`vehicle.yaml`](../lhr_vehicle/README.md). Wheel speeds follow body
motion exactly, so wheel slip never appears. Front speeds ignore
steering and are approximate in tight turns; the EKF reads only the rear
pair.

| Parameter | Default | What it does |
| --- | --- | --- |
| `gyro_noise_std` | 0.01 | Yaw-rate noise, rad/s std dev |
| `gyro_bias` | 0.0 | Constant yaw-rate offset, rad/s |
| `accel_noise_std` | 0.1 | Acceleration noise, m/s² std dev |
| `accel_bias` | 0.0 | Constant longitudinal acceleration offset, m/s² |
| `wheel_speed_noise_std` | 0.05 | Wheel-speed noise, rad/s std dev |
| `wheel_scale_error` | 1.0 | Scale on reported wheel speed; 1.0 is a perfectly known rolling radius |
| `seed` | 0 | Seeds this node's random stream |
| `publish_imu` | true | Publish the synthetic IMU |

Gazebo sets `publish_imu` false because it bridges a physics IMU on the
same topic. Its synthetic IMU parameters therefore do nothing in Gazebo;
the Gazebo launch applies `gyro_bias` to the native physics sensor separately.
The wheel publisher is omitted when wheel fusion is disabled.

## gnss_sim

Simulation-only `gnss_sim` publishes noisy `sensor_msgs/NavSatFix` from the
plant's antenna position. Gazebo starts it only with
`estimator:=ekf use_gnss:=true simulate_gnss:=true`. This is a separate sensor
model: the EKF sees uncertain position fixes, never continuous truth odometry.
The existing truth start-pose initialization remains separate.

A local seeded generator adds independent white Gaussian ENU position error.
The message contains its actual diagonal variance, fix status, antenna frame
and measurement timestamp. Delivery is delayed without changing that stamp.
Only distinct plant samples are used: requesting 100 Hz from 50 Hz truth still
produces at most 50 Hz. No GPS heading, velocity, multipath, time-correlated
error, dropout or RTK convergence is simulated. These profiles are experimental
assumptions, not validation against the physical receiver.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `gnss_rate_hz` | vehicle YAML: 50 Hz | Maximum acquisition rate |
| `gnss_horizontal_stddev_m` | vehicle YAML: 0.8 m | Independent per-axis east/north sigma |
| `gnss_vertical_stddev_m` | vehicle YAML: 1.2 m | Up sigma; not fused by the planar EKF |
| `gnss_latency_s` | vehicle YAML: 0.05 s | Simulated delivery delay |
| `gnss_topic` / `gnss_frame_id` | `/lhr/gnss/fix` / `gnss_link` | Output and antenna identity |
| `gnss_origin_latitude_deg` / `gnss_origin_longitude_deg` / `gnss_origin_altitude_m` | 0/0/0 | WGS84 datum, degrees and ellipsoid metres |
| `seed` | 0 standalone; Gazebo's course seed | Node-local random stream |
| `truth_topic` | `/lhr/vehicle/odom_truth` | Private plant input |

All controls are typed Gazebo launch arguments in both perception modes.
`truth_topic` is deliberately private plant wiring, supplied by the launch
rather than exposed as a public argument. Antenna placement comes only from
`load_vehicle().gnss.position_m`; the simulator rotates its full 3D mount,
while the EKF currently compensates only the planar lever arm.
