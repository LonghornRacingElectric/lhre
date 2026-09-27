# lhr_state_estimation

The EKF that replaces ground-truth odometry. Everywhere else in the stack,
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
| Predict | IMU `linear_acceleration.x` | Propagates the pose and speed forward at `publish_hz` |
| Correct | IMU `angular_velocity.z` | Anchors the yaw rate |
| Correct | Rear wheel speeds | Anchors the longitudinal speed |

Prediction runs on the publish timer rather than on IMU arrival, so losing a
sensor degrades the estimate instead of freezing it.

**Nothing measures position.** Without GNSS there is no absolute reference,
so `x` and `y` are dead reckoned and their covariance grows without bound —
that is correct behavior, not a bug, and it is why the position covariance is
published. Gyro bias is not estimated for the same reason: with no absolute
heading reference it is unobservable, so a bias state would only random-walk.
Both change when GNSS lands.

The wheel conversion uses the rear pair only: they are unsteered, so their
rolling direction is the body x axis and no steering angle enters it. All four
wheel speeds ride on the sensor topic because the real car reports four (USM,
one CAN frame per corner) and slip detection will want them.

## Nodes

`ekf_node` — subscribes `/lhr/imu/data` (`sensor_msgs/Imu`) and
`/lhr/sensor/wheel_speeds` (`sensor_msgs/JointState`, rad/s per wheel),
publishes `/lhr/vehicle/odom` (`nav_msgs/Odometry`, twist in the body frame
per REP-103) and the `map → base_link` transform.

Pose and twist covariances are filled from the filter's own `P`, so a
consumer can tell how much to trust the estimate.

| Param | Default | Description |
|-------|---------|-------------|
| `imu_topic` | `/lhr/imu/data` | IMU input |
| `wheel_topic` | `/lhr/sensor/wheel_speeds` | Wheel-speed input |
| `odom_topic` | `/lhr/vehicle/odom` | Estimated odometry output |
| `publish_hz` | `50.0` | Predict + publish rate (Hz) |
| `frame_id` / `child_frame_id` | `map` / `base_link` | TF frames |
| `publish_tf` | `true` | Broadcast `map → base_link` |
| `wheel_radius` | from `vehicle.yaml` | Rolling radius (m) |
| `rear_wheel_names` | `['rl', 'rr']` | Joint names to read from the wheel message |
| `init_x` / `init_y` / `init_yaw` | `0.0` | Start pose |
| `init_pose_topic` | `''` | Seed the start pose from the first message here instead |
| `sigma_accel` | `1.0` | Process noise: linear acceleration (m/s²) |
| `sigma_yaw_accel` | `1.0` | Process noise: angular acceleration (rad/s²) |
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
`inertial_sim` synthesizes the sensors. Nothing downstream is reconfigured —
`track_builder`, `pure_pursuit`, `mission_manager` and `metrics` read the same
topic names they always did.

Dial in error to see the filter work for its living:

```bash
./scripts/run_demo.sh estimator:=ekf gyro_bias:=0.03 wheel_scale_error:=1.03
```

## Measuring it

`lhr_metrics` scores the estimate against truth when `truth_odom_topic` is
set, which both demos do. Three columns join `data/metrics.csv`:
`mean_pos_error`, `max_pos_error` (m) and `mean_yaw_error` (rad). They are
blank under `estimator:=truth`, since nothing publishes a truth topic then.

Measured on the kinematic autocross lap, default noise: mean 0.03 m,
max 0.24 m. With `gyro_bias:=0.03 wheel_scale_error:=1.03`: mean 5.2 m,
max 16.6 m.

Note that cross-track error barely moves between those runs. Perception,
the centerline and the controller all live in the *estimated* frame, so the
loop stays self-consistent while the car quietly drives somewhere else. The
position-error columns are what expose that; CTE alone will not.

## Tests

The filter math is free of rclpy, so it runs under plain pytest with no ROS
environment (an option [ADR-009](../../../../docs/architecture/009-autonomy-outside-bazel.md)
anticipated):

```bash
colcon test --packages-select lhr_state_estimation   # lint + unit tests
cd src/lhr_state_estimation && PYTHONPATH=. python3 -m pytest test -q
```

## Not done yet

- **GNSS.** The correction that bounds position drift. Needs a sim GNSS
  model first; see [on-car architecture](../../../docs/plans/on-car-architecture.md).
- **IMU lever arm.** The real IMU sits at the chassis origin, not at
  `base_link`; the centripetal and angular-acceleration terms that implies
  are ignored.
- **Front wheels and differential yaw rate.** Both are on the wire and unused.
- **Slip.** Wheel speed is trusted as ground speed. It will not be under
  braking or launch.
