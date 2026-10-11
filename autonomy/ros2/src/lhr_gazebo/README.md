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

## Stereo model

The generated vehicle carries one ZED 2i rig with the 2.1 mm lens: two ideal
RGB pinhole sensors, parallel axes, and a 0.12 m local-y separation. Both
sensors share `zed2i_link`, so rotating the mount preserves Gazebo's stereo
baseline calculation. Geometry and optics come from `lhr_vehicle`'s YAML;
regenerate the model after changing them.

The camera-only link has no collision and uses 0.001 kg mass / 0.000001 kg m2
diagonal inertia for simulation bookkeeping. The initial mount and optics
are approximations awaiting measurement and physical device calibration.

## Stereo images in ROS/RViz

Enable the camera independently of perception and state estimation:

```bash
./scripts/run_gazebo_demo.sh track_style:=oval perception:=lidar \
  estimator:=truth enable_stereo_camera:=true
```

`enable_stereo_camera` defaults to `false`. Enabling it starts a separate
lazy image/calibration bridge, publishes the rig's static optical transforms,
and selects an RViz configuration with left/right Image panels. The option
works with both `perception:=sim` / `lidar` and `estimator:=truth` / `ekf`.
Camera geometry and optics are model configuration, not ROS node parameters:
edit `lhr_vehicle/config/vehicle.yaml`, regenerate, rebuild, and relaunch.

| ROS topic | Type |
| --- | --- |
| `/lhr/sensor/zed2i/left/image_rect` | `sensor_msgs/msg/Image` |
| `/lhr/sensor/zed2i/right/image_rect` | `sensor_msgs/msg/Image` |
| `/lhr/sensor/zed2i/left/camera_info` | `sensor_msgs/msg/CameraInfo` |
| `/lhr/sensor/zed2i/right/camera_info` | `sensor_msgs/msg/CameraInfo` |

Use sensor-data QoS (best effort, volatile) for consumers. Images retain
Gazebo timestamps and optical frame IDs. Both views are ideal rectified RGB;
`CameraInfo` describes those rendered intrinsics, with right `P[3] = -fx * B`.
The static chain is `base_link -> zed2i_link -> left/right camera frames ->
optical frames` (optical x right, y down, z forward). The mount transform
continues to attach to the estimator-owned `base_link` in EKF mode.

The 30 Hz configured rate is in simulation time. Software rendering can
reduce wall-clock throughput; no subscribers means the lazy bridges do not
request images. Camera resources may still initialize at simulator startup.
This milestone supplies camera inputs for later classification/fusion.
