# lhr_safety

The software half of the safety loop from the PDR: `safety_node` watches the
stack and stops the car when it stops being trustworthy. The hardware half
(pit-phone heartbeat, BEVO timer, VCU watchdog, relay chain, front-circuit
valve) is the 12/2 bench rig's job; this node is what that loop will listen
to.

The rule it follows is the PDR's: software can keep the loop alive, it can
never hold it open. The heartbeat only advances while every check passes,
so a fault, a crash or a hang all look the same to whatever is listening.

## Checks

| Fault | Trips when | Reads |
|---|---|---|
| `ODOM_STALE` | No odometry for `odom_timeout_s` (the EKF or bridge died or hung) | `/lhr/vehicle/odom` |
| `EKF_DIVERGED` | Any non-finite field, speed over `max_speed_factor` x the car's top speed from `vehicle.yaml`, or (once enabled) position standard deviation over `max_position_std_m` | `/lhr/vehicle/odom` |
| `LIDAR_STALE` | No cloud for `lidar_timeout_s` (only with `monitor_lidar`) | `/lhr/lidar/points` |
| `GEOFENCE` | Further than `geofence_radius_m` from the first reported pose | `/lhr/vehicle/odom` |
| `STEER_TRACKING` | Above `steer_min_speed_mps`, measured yaw rate differs from v·tan(δ)/L for the commanded δ by more than `max(steer_yaw_rate_tol, steer_rel_tol x expected)` for `steer_window_s` | `/lhr/vehicle/cmd`, `/lhr/vehicle/odom` |

The uncertainty bound is off by default (`max_position_std_m: 0`). Without
GNSS the EKF's position variance starts at 1 m² and only grows, so any bound
trips before the car moves; the first Gazebo run on 10/18 did exactly that.
Turn it on once GNSS keeps the covariance bounded.

None of them read ground truth. The steering check stands in for a steering
angle sensor the sim does not have; on the car the same comparison runs off
the IMU yaw rate and catches a stuck or slow actuator. A sensor that never
reports counts as stale from node startup rather than being ignored.

The order above is the priority when several fire at once: a dead or
diverged pose explains everything downstream of it, so it is reported
first.

The checks live in
[`monitors.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/src/lhr_safety/lhr_safety/monitors.py)
with no ROS imports, so `test/test_monitors.py` exercises each one
directly. The node only feeds it timestamps and message fields.

## What a trip does

- A fault only trips the car while `/lhr/mission/status` is `READY` or
  `DRIVING`. Before that the stack is still coming up; after it the car is
  already stopped.
- A trip latches and publishes `true` on `/lhr/mission/emergency` every tick
  until `/lhr/mission/reset` clears it. Every tick, not once, so one dropped
  message cannot leave the car driving.
- `mission_manager` acts on the emergency only in `DRIVING`. A trip in
  `READY` holds until the first `DRIVING` tick, which then goes straight to
  `EMERGENCY`.

## Outputs

| Topic | Type | Meaning |
|---|---|---|
| `/lhr/safety/heartbeat` | `std_msgs/UInt32` | Counter, advanced and published only while healthy and not latched |
| `/lhr/safety/status` | `diagnostic_msgs/DiagnosticStatus` | Every tick: `OK` or `ERROR`, the fault name, and `code`, `latched`, `heartbeat`, `mission_state` values |
| `/lhr/mission/emergency` | `std_msgs/Bool` | `true` every tick while latched |

The fault codes (`Fault` in `monitors.py`) are what the CAN heartbeat will
carry once the message set is agreed with Electronics, so they are
append-only: never renumber or reuse one.

## Running

```bash
./scripts/run_gazebo_demo.sh perception:=lidar estimator:=ekf safety:=true
```

`safety:=true` is off by default until the fault-injection runs
([#117](https://github.com/LonghornRacingElectric/lhre/issues/117)) have
tuned the thresholds. Every threshold is a `safety_*` launch argument with
the same default as the node. `monitor_lidar` is not an argument: it follows
`perception`, since there is no cloud to go stale under `perception:=sim`.

## Not done here

- Normal braking is still the controller commanding speed 0, so stops in
  sim are optimistic. A regen and spring-brake model is the other half of
  [#116](https://github.com/LonghornRacingElectric/lhre/issues/116).
- Thresholds are starting points; #117 measures time to trip and stop
  distance against the PDR table.
- The geofence is a radius around the start. The car's will be the surveyed
  test-area boundary from RTK.

## Tests

```bash
colcon test --packages-select lhr_safety lhr_gazebo
colcon test-result --verbose
```

`lhr_gazebo`'s `test_safety_launch.py` checks the launch defaults, overrides
and types against the node.
