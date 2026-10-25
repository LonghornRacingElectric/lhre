# lhr_safety

The software half of the safety loop from the PDR: `safety_node` watches the
stack and stops the car when it stops being trustworthy. The hardware half
(pit-phone heartbeat, BEVO timer, VCU watchdog, relay chain, front-circuit
valve) is the 12/2 bench rig's job; this node is what that loop will listen
to.

The rule it follows is the PDR's: software may only ever keep the stop loop
from firing by actively saying all is well; it can never block a stop. The
heartbeat only advances while every check passes and nothing is latched, so
a fault, a crash or a hang all look the same to whatever is listening.
`mission_manager` already listens: with `safety:=true`, READY waits for
`safety_settle_s` (2.0 s) of heartbeat with no gap over 0.25 s.

## Checks

| Fault | Trips when | Reads |
|---|---|---|
| `ODOM_STALE` | No odometry for `odom_timeout_s` (the EKF or bridge died or hung) | `/lhr/vehicle/odom` |
| `EKF_DIVERGED` | Any non-finite field, speed over `max_speed_factor` x the car's top speed from `vehicle.yaml`, or (once enabled) position standard deviation over `max_position_std_m` | `/lhr/vehicle/odom` |
| `CMD_STALE` | No `/lhr/vehicle/cmd` for `cmd_timeout_s`. Once it has a mission status, `pure_pursuit` publishes at 20 Hz, so silence means the controller died while the actuators hold its last command | `/lhr/vehicle/cmd` |
| `CMD_INVALID` | A non-finite steering command, which the Gazebo adapter would clamp to full lock | `/lhr/vehicle/cmd` |
| `LIDAR_STALE` | No cloud for `lidar_timeout_s` (only with `monitor_lidar`) | `/lhr/lidar/points` |
| `GEOFENCE` | Further than `geofence_radius_m` from where the car armed (entered `READY`) | `/lhr/vehicle/odom` |
| `STEER_TRACKING` | Above `steer_min_speed_mps` forward or reverse, the odometry's yaw rate differs from v·tan(δ)/L for the commanded δ by more than `max(steer_yaw_rate_tol, steer_rel_tol x expected)` for `steer_window_s` | `/lhr/vehicle/cmd`, `/lhr/vehicle/odom` |

The order above is the priority when several fire at once: a dead or
diverged pose explains everything downstream of it, so it is reported
first. None of the checks read ground truth. A topic that never arrives
counts as stale from node startup, and a clock that jumps backwards (a sim
reset) counts as stale rather than as perfectly fresh.

Odometry arrives faster than the 20 Hz checks run, so a bad sample (a NaN, a
speed spike, a step outside the fence) is held until the next check reads
it rather than being overwritten by the good sample after it.

The uncertainty bound is off by default (`max_position_std_m: 0`). Without
GNSS the EKF's position variance starts at 1 m² and only grows, so any bound
trips before the car moves; the first Gazebo run on 10/18 did exactly that.
Turn it on once GNSS keeps the covariance bounded.

The steering check stands in for a steering angle sensor the sim does not
have. It reads the yaw rate in `/lhr/vehicle/odom`, which under
`estimator:=ekf` is the EKF's estimate, not the gyro itself, so it freezes
if the IMU dies. IMU and wheel-speed dropouts are not caught yet
([#126](https://github.com/LonghornRacingElectric/lhre/issues/126)).

The checks live in
[`monitors.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/src/lhr_safety/lhr_safety/monitors.py)
and what the node does with them in
[`latch.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/src/lhr_safety/lhr_safety/latch.py),
both without ROS imports, so `test/test_monitors.py` and `test/test_latch.py`
exercise them directly. The node only feeds them messages and publishes the
result.

## What a trip does

- A fault only trips the car while `/lhr/mission/status` is `READY` or
  `DRIVING`. Before that the stack is still coming up; after it the car is
  already stopped. A fault still stops the heartbeat in any state.
- The first fault latches and publishes `true` on `/lhr/mission/emergency`
  every tick until `/lhr/mission/reset` clears it. Every tick, not once, so
  one dropped message cannot leave the car driving.
- `mission_manager` goes to `EMERGENCY` from either `READY` or `DRIVING`, and
  ignores an emergency that arrived while `OFF`, so the tail of a cleared
  trip cannot stop the next run. A check that is still failing after reset
  withholds the heartbeat, so with `require_safety_heartbeat` (the Gazebo
  launch sets it from `safety:=true`) the mission manager stays in `OFF`
  instead of re-arming. Without it, the check trips again as soon as the
  mission reaches `READY`.
- Both nodes subscribe to the reset, so publish it with `-w 2`, or one of them
  may miss it:

```bash
ros2 topic pub --once -w 2 /lhr/mission/reset std_msgs/msg/Bool "{data: true}"
```

## Outputs

| Topic | Type | Meaning |
|---|---|---|
| `/lhr/safety/heartbeat` | `std_msgs/UInt32` | Counter, advanced and published only while healthy and not latched |
| `/lhr/safety/status` | `diagnostic_msgs/DiagnosticStatus` | Every tick: `OK` or `ERROR` and the latched (else live) fault name, plus `code`, `latched`, `live`, `heartbeat`, `mission_state` values |
| `/lhr/mission/emergency` | `std_msgs/Bool` | `true` every tick while latched |

The fault codes (`Fault` in `monitors.py`) are what the CAN heartbeat will
carry once the message set is agreed with Electronics, so they are
append-only: never renumber or reuse one.

## Running

```bash
./scripts/run_gazebo_demo.sh perception:=lidar estimator:=ekf safety:=true
```

On a CPU-rendered machine (the Mac Docker container) Gazebo drops LiDAR
frames: the cloud arrived at 5.6 Hz with gaps up to 1.1 s on 10/18 while
the ZED cameras always rendered (#123), and still has gaps up to about
0.7 s with them off since #160. The default
`lidar_timeout_s` (0.3 s, three missed frames) is for the real Mid-360, so
add `safety_lidar_timeout_s:=1.5` there:

```bash
./scripts/run_gazebo_demo.sh gui:=false perception:=lidar estimator:=ekf safety:=true safety_lidar_timeout_s:=1.5
```

`safety:=true` is off by default until the fault-injection runs
([#117](https://github.com/LonghornRacingElectric/lhre/issues/117)) have
tuned the thresholds. Every threshold is a `safety_*` launch argument with
the same default as the node. Three parameters are deliberately not
arguments:

- `monitor_lidar` follows `perception`: the Gazebo LiDAR always publishes,
  but under `perception:=sim` nothing reads the cloud, so its age says
  nothing about what the car can see.
- `rate_hz` (20) is the check and heartbeat rate, not a threshold; the
  timeouts are what get tuned.
- `lidar_topic` is fixed by the bridge config.

## Not done here

- In sim the stop goes through `pure_pursuit` commanding speed 0. After a
  `CMD_STALE` trip that node is the dead one, so the stop comes from
  `joint_cmd_adapter`'s own command timeout (`adapter_cmd_timeout_s`) instead.
  On the car the hardware loop opens regardless.
- Normal braking is still the controller commanding speed 0, so stops in
  sim are optimistic. A regen and spring-brake model is the other half of
  [#116](https://github.com/LonghornRacingElectric/lhre/issues/116).
- A dead `mission_manager` is not detected
  ([#127](https://github.com/LonghornRacingElectric/lhre/issues/127)).
- Thresholds are starting points; #117 measures time to trip and stop
  distance against the PDR table with
  [the fault injection tools](../../../testing/tools/fault_injection/README.md).
- The geofence is a radius around where the car armed. The car's will be
  the surveyed test-area boundary from RTK.

## Tests

```bash
colcon test --packages-select lhr_safety lhr_mission_manager lhr_gazebo
colcon test-result --verbose
```

`lhr_gazebo`'s `test_safety_launch.py` checks the launch defaults, overrides
and types against the node, and `lhr_mission_manager`'s
`test_state_machine.py` covers the emergency and reset transitions.
`lhr_mission_manager`'s `test_safety_settle.py` and `lhr_gazebo`'s
`test_safety_prearm.py` cover the heartbeat wait before READY.
