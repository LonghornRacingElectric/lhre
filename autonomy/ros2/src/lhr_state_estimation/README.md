# lhr_state_estimation

The IMU-based EKF that replaces ground-truth odometry. Wheel-speed corrections
are disabled by default because the car's wheel-speed feedback is unreliable.
`use_wheel_speeds:=true` is a legacy diagnostic comparison, not the intended
real-car configuration. The default creates no wheel-speed subscription;
Gazebo also omits the truth-derived wheel-speed publisher in this mode.

Everywhere else in the stack,
`/lhr/vehicle/odom` comes from the simulator and is exact; on the car nothing
will hand us that. This package estimates it from sensors instead, so the
upper stack can be exercised against a pose that is wrong in the ways a real
one will be.

Ground truth stays available on `/lhr/vehicle/odom_truth` whenever the
estimator is running, which is what makes the error measurable rather than
merely plausible.

## The filter

State is `[x, y, yaw, v, yaw_rate]`.

| Step | Source | What it does |
|------|--------|--------------|
| Predict | IMU `linear_acceleration.x` | Propagates at each ordered IMU timestamp, using trapezoidal acceleration integration |
| Correct | IMU `angular_velocity.z` | Anchors the yaw rate |
| Optional correct | GNSS `NavSatFix` | Corrects antenna position with reported covariance, then replays subsequent IMUs |
| Optional correct | Rear wheel speeds | Anchors longitudinal speed only with `use_wheel_speeds:=true` |

Every IMU sample advances the state; `publish_hz` controls only odometry/TF
output. A short impulse between publish ticks is therefore retained. The first
sample establishes the time origin, and duplicate/out-of-order stamps are
ignored. A timestamp gap over 0.1 s logs a warning. During an IMU dropout the
node retains its last state **and sensor timestamp**, rather than repeatedly
integrating stale acceleration or presenting the state as freshly predicted.
No odometry is published until both initialization and an IMU sample exist.
The subscription buffers 1000 IMU messages for startup/backlog handling.
Process noise uses continuous-time acceleration intensities; covariance no
longer shrinks artificially when a time interval is split into more prediction
steps. The `sigma_accel` and `sigma_yaw_accel` names/defaults remain, but now
represent square-root process spectral intensities rather than per-step noise
standard deviations. Existing custom tuning needs review under these units.

**By default nothing measures position.** Without `use_gnss:=true` there is no absolute reference,
so `x` and `y` are dead reckoned and their covariance grows without bound —
that is correct behavior, not a bug, and it is why the position covariance is
published. The core has no gyro-bias state. Optional startup gyro-offset
subtraction is described below; continuous bias estimation would need further
independent information, such as verified standstill or reliable differential
wheel motion with slip checks. Absolute position still needs an
external reference.

The wheel conversion uses the rear pair only: they are unsteered, so their
rolling direction is the body x axis and no steering angle enters it. All four
wheel speeds ride on the sensor topic because the real car reports four (USM,
one CAN frame per corner) and slip detection will want them.

Without wheel updates there is no speed measurement either: acceleration
bias is integrated into velocity and then position. The historical wheel/IMU
numbers below do not describe this default. Optional startup gyro calibration
is simulator-tested below; acceleration calibration and continuous bias-state
estimation remain open. GNSS is an explicit opt-in; cone-map corrections are not implemented.

The [IMU-only checkpoint](../../../testing/2026-11-08-native-imu/README.md#imu-only-checkpoint)
found missing acceleration during smooth driving. Force-based actuation
[recovered that signal](../../../testing/2026-11-08-native-imu/README.md#force-based-actuation-fix),
but a later
[stopped-car investigation](../../../testing/2026-11-08-native-imu/README.md#stopped-car-investigation)
confirmed that the former 100 Hz sensor could still alias sharp deceleration
events and the former 50 Hz prediction timer discarded intervening samples.
Gazebo acquisition now runs at 1000 Hz and this node integrates every ordered
sample. Continuous bias-state, gravity/attitude and lever-arm compensation
remain unimplemented; optional startup gyro-offset subtraction is available below.
The wheel-free default is not validated for driving. The legacy wheel option
does not substitute for reliable real-car measurements.

At rest the EKF does not reset speed: zero acceleration preserves the existing
speed estimate, and acceleration bias changes it further. A stuck vehicle
can therefore continue moving in estimated odometry and TF. A small IMU
acceleration or a zero command is not sufficient evidence of zero vehicle
velocity; steady translation or coasting can produce the same inputs. Any
zero-velocity correction needs independent stationary evidence. Simulator
truth and contact events used to diagnose this are not estimator inputs.

## Nodes

`ekf_node` — subscribes `/lhr/imu/data` (`sensor_msgs/Imu`) and, only when
explicitly enabled, `/lhr/sensor/wheel_speeds` (`sensor_msgs/JointState`, rad/s per wheel),
publishes `/lhr/vehicle/odom` (`nav_msgs/Odometry`, twist in the body frame
per REP-103) and the `map → base_link` transform.

The planar pose (`x`, `y`, `yaw`) and twist (`v`, `yaw_rate`) covariance entries
come from the filter's `P`. This is a 2D estimate: the unmodelled z, roll,
pitch, lateral and vertical velocity entries remain zero in the ROS odometry
covariance arrays and must not be interpreted as measured certainty.

| Param | Default | Description |
|-------|---------|-------------|
| `imu_topic` | `/lhr/imu/data` | IMU input |
| `wheel_topic` | `/lhr/sensor/wheel_speeds` | Wheel-speed input |
| `use_wheel_speeds` | `false` | Opt into the legacy wheel-speed correction |
| `use_gnss` | `false` | Fuse horizontal `sensor_msgs/NavSatFix`; requires wheel speeds disabled |
| `gnss_topic` | `/lhr/gnss/fix` | GNSS measurement input |
| `gnss_frame_id` | `gnss_link` | Required input antenna frame; mount comes from vehicle YAML |
| `gnss_origin_latitude_deg` / `gnss_origin_longitude_deg` / `gnss_origin_altitude_m` | `0.0` | Fixed WGS84 datum, ellipsoid height; map x=east, y=north |
| `gnss_max_delay_s` | `1.0` | Retained IMU history for delayed fixes |
| `gnss_gate_chi2` | `25.0` | Maximum squared Mahalanobis position innovation |
| `gnss_heading_gain` | `1.0` | Experimental scale [0,1] on GPS yaw/yaw-rate gain rows; position/speed gain rows unscaled |
| `odom_topic` | `/lhr/vehicle/odom` | Estimated odometry output |
| `publish_hz` | `50.0` | Odometry/TF output rate; prediction follows IMU timestamps |
| `frame_id` / `child_frame_id` | `map` / `base_link` | TF frames |
| `publish_tf` | `true` | Broadcast `map → base_link` |
| `wheel_radius` | from `vehicle.yaml` | Rolling radius (m) |
| `rear_wheel_names` | `['rl', 'rr']` | Joint names to read from the wheel message |
| `init_x` / `init_y` / `init_yaw` | `0.0` | Start pose |
| `init_pose_topic` | `''` | Seed the start pose from the first message here instead |
| `sigma_accel` | `1.0` | Square root of linear-acceleration process intensity (m/s^1.5) |
| `sigma_yaw_accel` | `1.0` | Square root of angular-acceleration process intensity (rad/s^1.5) |
| `speed_variance` | `0.01` | Wheel-speed measurement variance (m/s)² |
| `yaw_rate_variance` | `0.0004` | Gyro measurement variance (rad/s)² |

`init_pose_topic` stands in for the survey or GNSS fix that will seed the
filter on the car. The kinematic demo passes an explicit start pose because
the launch file sets it; the Gazebo demo seeds from truth because the world
file chooses where the car spawns.

## Running it

Both demos take `estimator:=truth|ekf`. `truth` is the default and changes
nothing.

```bash
./scripts/run_demo.sh estimator:=ekf
./scripts/run_gazebo_demo.sh estimator:=ekf track_style:=oval
```

Under `ekf` the simulator's ground truth is remapped to
`/lhr/vehicle/odom_truth` and `/tf_truth`, the estimator takes over
`/lhr/vehicle/odom` and `/tf`, and
[`lhr_sensor_sim`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/src/lhr_sensor_sim/lhr_sensor_sim/inertial_sim_node.py)'s
`inertial_sim` synthesizes the sensors in the kinematic demo. In Gazebo it
publishes only wheel speeds; the physics IMU supplies native noise and bias
configured in vehicle YAML. Gazebo's `gyro_bias` now adds a signed z-gyro
offset to that actual sensor, and `imu_noise:=false` disables its nominal
noise and startup bias for comparison. See
[native IMU controls](../lhr_gazebo/README.md#native-imu-noise-and-bias).
`track_builder`, `pure_pursuit`,
`mission_manager` and the LiDAR detector read the same topic names they
always did, so they run on the estimate. Two consumers move to truth on
purpose: the kinematic demo's `lidar_sim` renders from truth odometry, so
the scene stays put while the estimate drifts, and `metrics` scores
cross-track error, progress and the lap on truth.

Dial in error to see the filter work for its living:

```bash
./scripts/run_demo.sh estimator:=ekf gyro_bias:=0.03 wheel_scale_error:=1.03
```

## Periodic GNSS correction

Gazebo supports `estimator:=ekf use_gnss:=true`; see the
[launch controls](../lhr_gazebo/README.md#periodic-gnss). Other demos retain
GNSS-off behavior. All GNSS parameters are startup settings.

Fixes use a fixed WGS84-to-ENU datum and the antenna's planar lever arm from
`load_vehicle().gnss.position_m`. On hardware, survey that mount and datum,
align the map to ENU, initialize heading independently, and supply timestamped
`NavSatFix` from the receiver driver. The virtual default antenna at
`base_link` and the synthetic 0/0/0 datum are simulation assumptions.
Vertical position and roll/pitch lever-arm effects are not fused.

Each fix corrects the historical state at its measurement time, then replays
later IMU prediction and gyro updates. This avoids treating a delayed position
as the current position. Accepted corrections affect future odometry/TF;
previously published transforms and accumulated cone observations are not
rewritten. GNSS and legacy wheel updates cannot be enabled together because
the replay history retains only IMU events.

The filter requires a valid fix status, the configured antenna frame, finite
coordinates and known, positive-definite horizontal ENU covariance. Duplicate,
reordered, too-old and inconsistent fixes are rejected; future fixes wait for
the IMU clock within the configured history limit. Decisions are published as
JSON strings on `/lhr/state_estimation/gnss_status` with measurement/state
stamps, acceptance, reason and normalized innovation squared (`nis`). Without
fixes the IMU continues dead reckoning; no truth fallback is applied. Gazebo
still uses its existing one-time truth start-pose initialization.

Position fixes can correct correlated speed/heading errors while moving, but
do not measure heading at standstill. Bias states, attitude compensation,
relocalization after large errors, correlated GNSS error and map consistency
remain open work. The
[GNSS experiment](../../../testing/2026-11-08-native-imu/README.md#periodic-gnss-experiment)
reduced pre-contact position error but did not eliminate cone collisions.

## Measuring it

`lhr_metrics` scores the estimate against truth when `truth_odom_topic` is
set, which both demos do. Three columns join `data/metrics.csv`:
`mean_pos_error`, `max_pos_error` (m) and `mean_yaw_error` (rad). They are
blank under `estimator:=truth`, since nothing publishes a truth topic then.

Measured on the kinematic autocross lap, default noise: mean 0.03 m,
max 0.24 m. With `gyro_bias:=0.03 wheel_scale_error:=1.03`: mean 5.2 m,
max 16.6 m. Both were measured before #154 moved scoring to truth and #168
compared each estimate with truth interpolated at the estimate's own
stamp, so current rows are not directly comparable.

Cross-track error barely moved between those runs, because it was then
scored from the estimate, the same pose the controller steers by: the
loop stayed self-consistent while the car quietly drove somewhere else.
Under `ekf` metrics now scores CTE, progress and the lap on truth, so
drift shows up there and can fail the run. The position-error columns
still say how much of it the estimate caused.

## Tests

The filter math is free of rclpy, so it runs under plain pytest with no ROS
environment (an option [ADR-009](../../../../docs/architecture/009-autonomy-outside-bazel.md)
anticipated):

```bash
colcon test --packages-select lhr_state_estimation   # lint + unit tests
cd src/lhr_state_estimation
PYTHONPATH=. python3 -m pytest test/test_ekf.py test/test_gnss.py test/test_heading_gain.py test/test_gyro_calibration.py -q
```

## Not done yet

- **Physical GNSS integration.** The NavSatFix input and simulator exist;
  a receiver/BEVO adapter, measured covariance, clock alignment, surveyed
  datum/mount and startup heading still need hardware validation. See
  [on-car architecture](../../../docs/plans/on-car-architecture.md).
- **IMU lever arm.** The real IMU sits at the chassis origin, not at
  `base_link`; the centripetal and angular-acceleration terms that implies
  are ignored.
- **Front wheels and differential yaw rate.** Both are on the wire and unused.
- **Slip.** Wheel speed is trusted as ground speed. It will not be under
  braking or launch.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

### GNSS heading-update diagnostics

`/lhr/state_estimation/gnss_status` now also reports the heading and
longitudinal speed immediately before/after each accepted correction. Fields
prefixed `fix_` describe the update at the historical measurement time;
`current_` describes the state before correction versus after IMU replay at
the same current sensor time. `*_yaw_delta_rad` is signed and wrapped to
[-pi, pi). `fix_yaw_position_cross_covariance` holds the pre-update yaw/east
and yaw/north covariance entries. Rejected pre-validation/old fixes omit
these optional fields; rejection is not an applied heading jump.

A position-only fix can change yaw through cross-covariance even with a zero
antenna lever arm. These diagnostics expose that effect; they do not change
the update, constrain yaw, apply stationary corrections or calibrate the gyro.
The [heading attribution experiment](../../../testing/2026-11-08-native-imu/README.md#gps-heading-jitter-attribution)
separates steady gyro drift from GPS-driven heading jumps with independent
stationarity checks and held-out offline calibration. Its gyro-only
correction does not eliminate stationary GPS wobble. Live startup gyro
calibration is now available below; a verified zero-velocity correction remains open.

## Experimental heading controls

Gazebo forwards `gnss_heading_gain` and the existing `yaw_rate_variance` to
this node in both perception modes, with explicit float types. Defaults remain
1.0 and 0.0004 (rad/s)². Values must be finite, with gain in [0,1] and gyro
variance positive. These are startup parameters. Metrics records both values.

`gnss_heading_gain` multiplies the **yaw and yaw-rate rows** of the GNSS Kalman
gain before updating the state. Joseph covariance uses that same modified
gain, retaining symmetry/positive semidefiniteness in the tested cases. It is
a deliberately non-optimal gain experiment, not a claim that the approximate
motion model is statistically consistent under every condition. Immediate
position/speed gains and GPS measurement covariance are unscaled; future
position estimates can still differ through coupled dynamics.

Zero gain preserves gyro-driven rotation but removes heading recovery through
GPS. It does not freeze heading. Reducing gyro measurement variance alone
gives angular rate more confidence, while GPS can still change yaw through
position/yaw cross-covariance. Neither control estimates a gyro bias, supplies
absolute starting heading, verifies standstill or adds velocity constraints.

The [policy comparison](../../../testing/2026-11-08-native-imu/README.md#gyro-confidence-and-gps-heading-gain-comparison)
uses calibrated offline inputs, uncertain starting headings and recorded/
synthetic moving controls. Lower GPS angular gain improves stationary heading
precision but delays recovery from a starting-heading error. A permanent zero
or small gain is not validated for real-car operation with uncertain initial
heading. Live startup calibration is now available below; a sensor-verified
adaptive heading policy remains open. Both controls remain opt-in.

## Live startup gyro calibration

`calibrate_gyro:=true` enables a one-time gyro-z offset estimate from raw IMU
and raw LiDAR data. The default remains false. The node withholds odometry/TF
until calibration succeeds; it does not publish a frozen pose as a valid live
estimate. In the supplied LiDAR/safety demo this prevents path initialization
and normal mission start until sensor initialization completes.

After the settling interval, a candidate window needs quiet three-axis gyro
and acceleration statistics and stable raw LiDAR geometry. The LiDAR check
uses canonical mount/body geometry, a base-frame height band 0.05–0.55 m and
sensor ranges 0.8–20 m. It downsamples planar points at 0.08 m, then estimates
rigid scan motion against the window anchor. At least 70% of points must have
correspondences within 0.3 m, the post-fit p90 residual must be <=0.08 m, and
both planar covariance eigenvalues must be >=0.1 m². These are experimental
observability/registration guards, not physical-car calibration validation.

The input must be raw XYZ in the canonical sensor frame/mount, not an
EKF-registered cloud or cone map. A frame change, detected motion, poor
registration, nonfinite IMU, nonquiet window or overall timeout fails closed
and requires a restart. Sensor gaps discard the candidate window; a complete
new window must pass within the original deadline. Final IMU/LiDAR timestamps
must agree within the maximum gap, so stale scene evidence cannot release the
filter. Geometry symmetry, dynamic clutter, subthreshold motion and mounting
errors remain limits. The operator must hold the vehicle still through
startup with a valid initial pose; this is not an unrestricted physical-car
standstill detector.

Only the gyro-z mean is subtracted. Acceleration, attitude, sensor noise and
bias-state estimation are unchanged. The learned offset is frozen for the
launch; it does not compensate subsequent thermal changes. Corrected gyro
samples are also retained in the GNSS replay history, preventing delayed
fixes from reintroducing raw gyro bias. Calibration LiDAR subscription is
removed after success.

`/lhr/state_estimation/gyro_calibration` is a reliable, transient-local JSON
String status: waiting/collecting/ready/failed, reason, restart count, sensor
stamps and, when ready, the offset, exact window, sample counts, noise spreads
and maximum LiDAR motion. The terminal logs `Gyro calibration ready` with the
offset. Gazebo forwards every parameter below with explicit types in both
perception branches. All are startup controls; none is a runtime parameter reset.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `calibrate_gyro` | false | Enable live startup calibration |
| `gyro_calibration_duration_s` | 3.0 | Minimum stationary window |
| `gyro_calibration_settle_s` | 2.0 | Initial settling interval |
| `gyro_calibration_timeout_s` | 15.0 | Overall deadline, greater than settle plus duration |
| `gyro_calibration_max_gap_s` | 0.25 | Maximum sensor gap/final relative age |
| `gyro_calibration_max_translation_m` | 0.03 | Maximum anchor-relative planar LiDAR motion |
| `gyro_calibration_max_yaw_deg` | 0.15 | Maximum anchor-relative LiDAR rotation |
| `gyro_calibration_max_gyro_std_rad_s` | 0.01 | Maximum per-axis gyro standard deviation |
| `gyro_calibration_max_accel_std_m_s2` | 0.15 | Maximum per-axis acceleration standard deviation |
| `gyro_calibration_max_gyro_mean_rad_s` | 0.03 | Maximum per-axis absolute gyro mean |
| `gyro_calibration_min_samples` | 200 | Minimum IMU samples, integer >=2 |
| `gyro_calibration_min_lidar_points` | 30 | Minimum downsampled points, integer >=20 |
| `gyro_calibration_lidar_topic` | `/lhr/lidar/points` | Independent raw LiDAR input |
| `gyro_calibration_lidar_qos` | `reliable` | `reliable` for Gazebo's bridge; `sensor_data` for best-effort hardware publishers |

The calibration cloud subscription buffers 50 frames. Gazebo requires the
reliable profile to avoid the observed startup burst losses; the earlier
best-effort depth-5 prototype repeatedly restarted or timed out. Motion and
freshness thresholds were preserved. ROS-node tests require ROS; only the
math tests in the command above can run without rclpy.

The [visual profile](../lhr_gazebo/README.md#calibrated-gyro-heading-visual-profile)
combines this calibration with `gnss_heading_gain:=0.0`: heading changes come
from corrected gyro rate, while GPS corrects position/speed. The usual
one-time Gazebo starting pose is still used. This cannot recover an erroneous
starting heading, replace a survey/alignment procedure or bound long-term
heading drift. The [live results](../../../testing/2026-11-08-native-imu/README.md#live-calibrated-gyro-heading-and-gps-position-visual-profile)
are short simulator experiments, not physical-car or full-gate certification.
