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
| `STEER_TRACKING` | The measured steering angle differs from the command by more than `steer_angle_tol_rad` for `steer_angle_window_s`, after allowing the actuator `vehicle.yaml`'s rate limit to get there; or the measured angle stops arriving, or never arrives, for `steer_angle_timeout_s`. The yaw-rate fallback below runs only with `require_steer_measurement` off | `/lhr/vehicle/cmd`, `/lhr/vehicle/steering_measured` (`/lhr/vehicle/odom` for the fallback) |

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

The steering check compares the measured steering angle with the command
([#208](https://github.com/LonghornRacingElectric/lhre/issues/208)). On the
car the angle comes from the steering motor's encoder; in Gazebo,
`joint_cmd_adapter` publishes the steering joints' actual angles as the
same single bicycle-model angle (`/lhr/vehicle/steering_measured`,
`sensor_msgs/JointState`, one joint named `steering`). The command is
clamped to `vehicle.yaml`'s steering lock and rate-limited at its
`max_rate_rad_s` before the comparison, so a saturated actuator or a step it
is still following is not a fault. 0.035 rad (about 2°)
for 0.2 s is ten 50 Hz samples' worth of a stuck actuator, not transport
delay. A measured angle that stops arriving, or never arrives, trips too, since
the steering can no longer be checked. `require_steer_measurement` (on by
default) makes a missing measurement stale from startup like every other
stream, so a wrong topic name or an encoder that is dead at boot keeps the
car from arming instead of quietly handing the check to the weaker rule.
It is not a Gazebo launch argument: Gazebo always publishes the measured
angle, so requiring it is never optional there.

The 11/1 fault matrix showed why the yaw rate was not enough. The old rule
compared it with v·tan(δ)/L inside a band of 0.15 rad/s or half the
expected rate, for 1 s: a freeze mid-corner kept widening the band while
the controller asked for more steering, and at 5.3 m/s the car left the
course before the 1 s window filled. That rule remains only as the
fallback for a plant with no steering measurement, with
`require_steer_measurement` off, from `steer_min_speed_mps` up. It reads the yaw rate in `/lhr/vehicle/odom`,
which under `estimator:=ekf` is the EKF's estimate, not the gyro itself, so
it freezes if the IMU dies. IMU and wheel-speed dropouts are not caught yet
([#126](https://github.com/LonghornRacingElectric/lhre/issues/126)).

A stuck actuator is invisible while the command still asks for the angle it
is stuck at, and no check can see it. The fault tools time `steer_freeze`
from when the command first moves `steer_angle_tol_rad` off the frozen
angle, as well as from the injection.

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
the same default as the node. Five parameters are deliberately not
arguments:

- `monitor_lidar` follows `perception`: the Gazebo LiDAR always publishes,
  but under `perception:=sim` nothing reads the cloud, so its age says
  nothing about what the car can see.
- `rate_hz` (20) is the check and heartbeat rate, not a threshold; the
  timeouts are what get tuned.
- `lidar_topic` is fixed by the bridge config.
- `steering_measured_topic` is fixed by `joint_cmd_adapter`.
- `require_steer_measurement` stays on: Gazebo always publishes the measured
  steering angle, so a run without it is a broken run, not an option.

## Not done here

- In sim the stop goes through `pure_pursuit` commanding speed 0. After a
  `CMD_STALE` trip that node is the dead one, so the stop comes from
  `joint_cmd_adapter`'s own command timeout (`adapter_cmd_timeout_s`) instead.
  On the car the hardware loop opens regardless.
- Normal braking is still the controller commanding speed 0, so stops in
  sim are optimistic. A regen and spring-brake model is
  [#199](https://github.com/LonghornRacingElectric/lhre/issues/199).
- A dead `mission_manager` is not detected
  ([#127](https://github.com/LonghornRacingElectric/lhre/issues/127)).
- Thresholds are starting points; #117 measures time to trip and stop
  distance against the PDR table with
  [the fault injection tools](../../../testing/tools/fault_injection/README.md).
- The geofence is a radius around where the car armed. The car's will be
  the surveyed test-area boundary from RTK. In sim it defaults to 300 m so
  one fence holds every gate course
  ([#229](https://github.com/LonghornRacingElectric/lhre/issues/229)): no
  point on a closed course is farther from the start than half its lap, the
  rules checker caps laps at 500 m, and acceleration reaches 152 m with its
  braking zone. Measured from spawn, the farthest cones are 184 m on the
  500 m extreme scenario, 147 m on the worst of 1000 generated
  rules-compliant seeds, and 79 m on AMZ's FSG layout. `lhr_gazebo`'s
  `test_geofence_reach.py` fails on any committed world or generated gate
  course that comes within 20 m of the fence. The fault matrix's `geofence`
  case still shrinks it to 20 m on purpose.

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
