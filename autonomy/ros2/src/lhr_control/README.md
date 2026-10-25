# lhr_control

Pure pursuit steering and curvature-based speed planning. Consumes
`/lhr/track/centerline`, `/lhr/vehicle/odom`, and mission status; publishes
`/lhr/vehicle/cmd`. Vehicle geometry and steering limits come from
[lhr_vehicle](../lhr_vehicle/README.md).

## Closed tracks and local paths

`closed_path=true` supports a complete closed circuit and remains the node
default. Gazebo sets `closed_path=false` for both simplified and LiDAR
perception because both publish a car-anchored forward prefix. MVS LiDAR
mode also selects open paths; its simplified mode retains the closed-path
setting despite the same forward-prefix producer; that remaining topology
issue is tracked in [#92](https://github.com/LonghornRacingElectric/lhre/issues/92).
Open-path lookahead and curvature estimates do not wrap between
the two endpoints. Targets behind the vehicle are rejected. Empty paths, expired local
paths, or paths with no forward target produce a zero-speed command.

Open-path speeds respect acceleration limits from zero and decrease with the
available stopping distance to the path end, even below `v_min`. A missing
forward corridor therefore causes a stop rather than a turn back toward the
start of the path. These checks use only the perceived path, not track truth.

Past the finish, `/lhr/mission/complete` brakes to zero while the mission
is still Driving (Finished means stopped). Steering keeps following the
path so the car stops in its lane, and with no path or target it still
commands the stop rather than going quiet.

The car only moves while the mission status is Driving. With no status at
all the node runs freely, for tests and standalone use, unless
`wait_for_mission` is set. Both demo launches set it, since they always
start a mission manager: otherwise a path that arrives before the
manager's first status moves the car before the mission exists. It is
not a launch argument for that reason.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `closed_path` | `true` | Wrap lookahead and curvature for a complete circuit |
| `path_timeout_sec` | `1.0` | Stop if an open path is not updated within this time |
| `wait_for_mission` | `false` | Stay stopped until a mission manager says Driving (the demo launches set it) |

Other steering and speed parameters are listed in the
[workspace reference](../../README.md#lhr_control-pursuit_node).
The controller still assumes ideal vehicle state and actuation; closed-loop
simulation does not validate hardware behavior.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

## Tests

From the built ROS workspace, run `colcon test --packages-select lhr_control
--return-code-on-test-failure` and `colcon test-result --verbose`. The autonomy
CI workflow discovers the same tests without launching a simulator.

Analytic cases check zero steering on straight paths in different map headings,
signed circle curvature and steering, steering saturation, closed-path wrap,
and degenerate geometry. Speed checks cover the lateral acceleration budget,
speed clamps, acceleration/deceleration limits, adaptive lookahead, endpoint
stopping and mission gating. Translated and rotated circles check that steering
uses the vehicle frame. Existing local-path cases cover short horizons,
stale/empty paths, forward targets and acceleration from rest.

These tests exercise callbacks directly with ROS messages and captured commands;
they do not measure DDS delivery or physical tracking. The closed-path speed
floor remains `v_min`, including startup and curves whose lateral budget would
otherwise demand a lower speed. Open paths can command below that floor to
accelerate from rest or stop at their endpoint.
