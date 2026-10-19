# lhr_gazebo

Gazebo Harmonic vehicle, world, bridge, and launch integration for the ROS 2
autonomy stack. Vehicle dimensions come from `lhr_vehicle`; the generated SDF
test fails when the committed model no longer matches that configuration.

## Run

### Acceleration

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py track_style:=acceleration perception:=sim
ros2 launch lhr_gazebo gazebo_demo.launch.py track_style:=acceleration perception:=lidar
```

The committed `worlds/acceleration.sdf` contains the same blue, yellow, small
orange, and large orange cones as the ground-truth publisher. Regenerate from
`autonomy/ros2/` with
`python3 src/lhr_gazebo/scripts/generate_world.py --style acceleration`.
The fixed layout ignores seed. Vehicle staging uses the configured body nose,
0.30 m behind the start. Missing acceleration worlds fail explicitly rather
than silently selecting another event's world.

Both perception branches select `acceleration_planner`, acceleration mission
completion, and open-path pure pursuit automatically. LiDAR may remain
unclassified: the planner assigns sides geometrically in the initial vehicle
frame. All navigation uses detected cones. Stop progress uses odometry and
known staging/event length, not detection of a unique orange finish gate.

| Launch parameter | Default | Effect |
| --- | --- | --- |
| `acceleration_path_spacing_m` | `0.5` | Maximum interpolated path sample spacing |
| `acceleration_stop_distance_m` | `20.0` | Planned nose stop distance past finish, at least 1 m and less than 75 m |
| `input_timeout_sec` | `1.0` | Planner/control freshness timeout |

These parameters are forwarded as floats in both perception modes. For example,
`acceleration_path_spacing_m:=1 acceleration_stop_distance_m:=25 input_timeout_sec:=2`.
`path_closed` is selected internally from the event, not exposed as a separate
launch argument, to keep acceleration's controller consistent with its planner.

After building/sourcing, verify the complete Gazebo loop with:

```bash
python3 src/lhr_gazebo/scripts/verify_acceleration.py --perception sim --gui
python3 src/lhr_gazebo/scripts/verify_acceleration.py --perception lidar --gui --keep-running
```

Use an isolated `ROS_DOMAIN_ID` and `GZ_PARTITION` if other demos are running.
The verifier starts Gazebo, checks Driving → Finished, forward progress,
lane containment, detected path publication, a stationary exit-lane stop, and
zero final commands. It prints measured results and the launch-log path.
Omit `--gui` for server-only verification. A zero-speed command uses simulated
wheel control; hardware brakes and the independent EBS are not implemented here.

Validated on 2026-10-18 in the macOS Docker image (ROS 2 Jazzy, Gazebo
Harmonic, GUI enabled), with default 5 m/s speed and 20 m exit target:

| Detected-cone source | Peak speed | Nose stop past finish | Maximum lateral deviation | Result |
| --- | --- | --- | --- | --- |
| Synthetic sensor | 5.00 m/s | 19.57 m | <0.001 m | Finished, stationary |
| Simulated LiDAR, unclassified | 5.00 m/s | 19.59 m | <0.001 m | Finished, stationary |

These straight, correctly staged, truth-odometry runs verify the simulation
pipeline. They do not establish real sensor accuracy, vehicle braking limits,
or performance with odometry drift.

### Closed tracks

```bash
cd autonomy/ros2
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar estimator:=truth
```

`perception:=sim` uses labelled generator cones and color-aware Delaunay
(`boundary`) pairing. The default `constrained_greedy` chaining orders its
midpoints from the vehicle using distance and heading limits.
`perception:=lidar` clusters the simulated pointcloud and maps unclassified
cones by default. The track builder uses `boundary` pairing. Use
`estimator:=truth` to check perception without EKF drift, then repeat with
`estimator:=ekf`.

To opt into left/right inference and one-to-one `classified` pairing, run:

```bash
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar lidar_classify_sides:=true estimator:=truth
```

By default, mapped cones appear orange in RViz. The unclassified `boundary`
strategy can produce incorrect centerlines on sharp autocross turns; the
classifier remains opt-in while its sparse-map failures are investigated.

The geometry-based side classifier is experimental. In a live autocross run,
the cone map had no duplicate cones, but sparse early detections caused a
right-side boundary fragment to be labelled left and the car departed the
course. The generated-track regression does not reproduce the LiDAR's
occlusion and sparse returns.

The launch starts graphical Gazebo and RViz by default. Use `gui:=false` or
`rviz:=false` for a headless run. See the workspace
[README](../../README.md) for all launch arguments and topic diagrams.

## Visual check

In RViz, inferred left cones are blue, inferred right cones are yellow, and
cones awaiting evidence are orange. During a sharp turn:

1. each physical cone should have one marker;
2. a marker should remain fixed in the map frame;
3. old orange markers should disappear when their side becomes known; and
4. the green centerline should remain a single ordered band without branches.

## Verification

```bash
cd autonomy/ros2
colcon test --packages-select lhr_gazebo
colcon test-result --verbose
```
