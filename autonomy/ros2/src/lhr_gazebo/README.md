# lhr_gazebo

Gazebo Harmonic vehicle physics, sensor bridging, and demo launch. Run
`ros2 launch lhr_gazebo gazebo_demo.launch.py` in a supported ROS environment.
`perception:=sim` uses simplified cone detections; `perception:=lidar` uses
[lhr_perception](../lhr_perception/README.md) and boundary planning.
LiDAR mode also selects open-path control so a partial corridor is not
treated as a complete circuit. See [the workspace reference](../../README.md)
for launch arguments and vehicle physics.

## LiDAR coordinates

The generated sensor SDF explicitly names its cloud frame `lidar`. The launch
publishes a static `base_link -> lidar` transform from
[lhr_vehicle](../lhr_vehicle/README.md)'s mount position. The sensor in this
model is level; changing its orientation requires updating both its generated
pose and transform. Detector height thresholds are in the ground-level
`base_link` frame: the launch uses `ground_z_min=0.05`, not a negative
sensor-frame ground threshold.

Gazebo still uses its existing uniform-grid VLP-16 model. The imported Livox
pattern belongs to the kinematic [Mid-360 simulator](../lhr_lidar_sim/README.md).

## Generated model

Edit the template or vehicle YAML, then run
`python3 src/lhr_gazebo/scripts/generate_vehicle_model.py` from the ROS workspace.
Commit the regenerated model alongside the source. The model consistency tests
run with `colcon test --packages-select lhr_gazebo` and need no GUI.

## Competition cone assets

The four cone models are generated from
[lhr_trackgen](../lhr_trackgen/README.md)'s shared nominal dimensions and
profile. They include square feet, colored stripe meshes and enabled
collision meshes. Regenerate from the ROS workspace with
`python3 src/lhr_gazebo/scripts/generate_cone_models.py`, then commit the
SDF and all three meshes per cone. Tests compare every generated asset.

Cones are static: a vehicle can collide with them, but they do not tip or
move. The stored masses describe the nominal assets and are not dynamic
physics parameters. Existing world origins remain at half cone height;
the generated mesh offsets place their feet at ground level. The MVS
optional gate layout is not added to existing generated Gazebo worlds.

The seed-1 autocross and oval fixtures use the corrected closed-boundary
spacing from [trackgen](../lhr_trackgen/README.md). Regenerate worlds after
changing track generation so the Gazebo scene matches MVS track truth.

## State estimation and optional side inference

## Run

```bash
cd autonomy/ros2
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar estimator:=truth
```

`perception:=sim` uses labelled generator cones and index pairing.
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
