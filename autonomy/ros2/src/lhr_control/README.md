# lhr_control

Pure pursuit follows `/lhr/track/centerline` using `/lhr/vehicle/odom` and
publishes `/lhr/vehicle/cmd`. Steering and curvature-based speed planning are
shared by the autocross and skidpad planners. Vehicle geometry
comes from `lhr_vehicle`.

## Acquisition mode

The Gazebo skidpad mission selects `path_closed:=false` and
`require_planner_limit:=true`. Open paths clamp lookahead and curvature
sampling to their endpoints instead of wrapping to the start. The controller
honors `/lhr/planning/speed_limit` even below `v_min`, including zero.
Missing or stale path, odometry or speed-limit input commands zero speed;
mission `DRIVING` is also required. A missing planner cannot leave the last
creep command active. Zero commands model a simulator stop, not a verified
hardware brake maneuver.

| Parameter | Node default | Description |
| --- | --- | --- |
| `path_closed` | `true` | Wrap path indices; false for the skidpad planner's local route windows |
| `require_planner_limit` | `false` | Require a fresh planner cap and input watchdogs |
| `planner_timeout_sec` | `0.5` | Maximum receipt age for required inputs |

These options are exposed by the Gazebo launch in both perception branches.
Autocross keeps the original closed-loop behavior with planner limiting off.
Other steering and speed parameters are in the [workspace reference](../../README.md).

Run `colcon test --packages-select lhr_control` from the ROS workspace.
