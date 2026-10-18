# lhr_gazebo

Gazebo Harmonic vehicle, world, bridge, and launch integration for the ROS 2
autonomy stack. Vehicle dimensions come from `lhr_vehicle`; the generated SDF
test fails when the committed model no longer matches that configuration.

## Run

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

## Foxglove and recording

```bash
./scripts/run_gazebo_demo.sh foxglove:=true rviz:=false perception:=lidar
./scripts/run_gazebo_demo.sh gui:=false record:=true run_id:=trial_001
```

`foxglove:=true` starts `foxglove_bridge` on `ws://localhost:8765`
(`foxglove_port` moves it). Load
[`foxglove/lhr_gazebo.json`](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/foxglove/lhr_gazebo.json)
as the layout: track, detected cones, centerline, cloud and car in 3D, plus
command vs actual speed, steering and mission state. It is
`foxglove/lhr_sim.json` minus the kinematic LiDAR sim's mount marker and
mount sliders, which Gazebo does not have.

`record:=true` writes MCAP to `data/bags/<run_id>` (`bag_dir` moves the
parent). The metrics row gets the same `run_id`, so a CSV row and its bag
can be matched. Use a new `run_id` per run; the default is a timestamp.

To see the layout, every run also starts:

- `publish_cones` in both perception modes. Under `perception:=lidar`
  nothing in the stack reads these generator cones; they are the ground
  truth a viewer and the scoring compare against.
- a static `base_link -> lidar` transform from `lhr_vehicle`'s mount, since
  the cloud is stamped in `lidar` (`gz_frame_id` in `model.sdf.in`).
- `vehicle_viz`, drawing the car from `vehicle.yaml`. `vehicle_mesh`
  defaults to `false` here, unlike the node, because the CAD mesh is not on
  main yet.

Check a recording against the layout before blaming the sim for empty
panels:

```bash
LAYOUT=foxglove/lhr_gazebo.json ./scripts/check_layout.sh data/bags/trial_001
```

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
