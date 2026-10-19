# Control

`pursuit_node` follows `/lhr/track/centerline` using pure pursuit and publishes
steering/speed on `/lhr/vehicle/cmd`. Vehicle geometry comes from `lhr_vehicle`.
See the [stack reference](../../README.md#lhr_control-pursuit_node) for tuning.

The default `path_closed=true` preserves loop following. The Gazebo launch
sets it to false for acceleration. Open-path lookahead and curvature clamp
at the final point, and the speed floor becomes zero. The speed ceiling uses
`/lhr/acceleration/remaining` and the observed path endpoint with a 0.3 m
endpoint margin. Braking uses `max_decel` plus 0.5 s of latency allowance;
normal acceleration/deceleration limits still apply.

`input_timeout_sec` defaults to 1.0 s and is exposed in the Gazebo launch.
In open-path mode, empty paths, missing odometry, missing/non-Driving mission
status, or stale path/odometry/remaining/status inputs publish zero speed.
Closed-path mode retains its existing behavior. Open-path mode currently
requires the acceleration remaining-distance topic; it is not a generic
standalone open-route controller.

Zero speed controls simulated wheel velocity. It does not implement the
vehicle's hydraulic brakes, independent emergency stop, or hardware watchdog.
