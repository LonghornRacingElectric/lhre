# lhr_gazebo

Gazebo Harmonic vehicle, world, bridge, and launch integration for the ROS 2
autonomy stack. Vehicle dimensions come from `lhr_vehicle`; the generated SDF
test fails when the committed model no longer matches that configuration.

## Run

```bash
cd autonomy/ros2
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar estimator:=truth
```

`perception:=sim` uses labelled generator cones and index pairing.
`perception:=lidar` clusters the simulated pointcloud, infers track sides from
vehicle-relative observations and boundary continuity, then uses one-to-one
classified pairing. Use `estimator:=truth` to check perception without EKF
drift, then repeat with `estimator:=ekf`.

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
