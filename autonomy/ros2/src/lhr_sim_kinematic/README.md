# lhr_sim_kinematic

A kinematic bicycle model: takes `AckermannDriveStamped` on
`/lhr/vehicle/cmd`, integrates position and heading at a fixed step, and
publishes `nav_msgs/Odometry` plus the `map` to `base_link` transform.

No tyres, no mass, no grip limits. Steering and speed commands take effect
instantly, so the car can turn at any speed without sliding. That is on
purpose: it makes the stack testable headless, on any OS, in seconds. The
car's real limits come from BobDyn (`simulation/`), not from here.

Geometry comes from `lhr_vehicle`, so wheelbase and steering limit are
never hardcoded.

## It is also the clock

This node is the plant, so it is the natural clock source. `_step()`
already advances by a fixed `dt` rather than by elapsed wall time, so with
`publish_clock` on it publishes that step as `/clock` and stamps odom and
TF from it.

The node keeps `use_sim_time` **false** for itself and its own timer on
wall time, which paces the run at roughly real time. A clock source that
waited on its own clock would never tick.

Starting sim time is 1 s, not 0, because a zero stamp reads as "unset"
downstream.

## Parameters

| Parameter | Default | What it does |
| --- | --- | --- |
| `wheelbase` | `lhr_vehicle` | Bicycle model wheelbase |
| `update_hz` | 50.0 | Step rate, and therefore the clock resolution |
| `max_steer` | `lhr_vehicle` | Steering command clamp |
| `max_speed` | `lhr_vehicle` | Speed command clamp |
| `frame_id` | `map` | Parent TF frame |
| `child_frame_id` | `base_link` | Child TF frame |
| `init_x`, `init_y`, `init_yaw` | 0.0 | Starting pose |
| `publish_clock` | false | Publish `/clock` and stamp from sim time |

## Gotcha

Turning `use_sim_time` on for other nodes without turning `publish_clock`
on here leaves them waiting for a clock that never arrives, and they will
sit silent rather than error. `mvs_demo.launch.py` drives both from one
argument so they cannot disagree.
