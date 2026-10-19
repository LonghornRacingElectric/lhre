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

For a stationary preview of the skidpad circular boundaries (no autonomy
nodes), source ROS and run from `autonomy/ros2`:

```bash
export GZ_SIM_RESOURCE_PATH="$PWD/src/lhr_gazebo/models${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
gz sim src/lhr_gazebo/worlds/skidpad_seed1.sdf
```

The preview has 17 inner and 13 outer cones per circle, small orange entry/
exit lanes and large orange gates. Orange cone spacing and lane lengths are
illustrative sim choices. The spawn's foremost vehicle envelope is 15 m before
the center line, using the current `lhr_vehicle` model footprint. Do not use
the autocross planner to drive this world.

## Skidpad event

After building and sourcing the workspace:

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py mission:=skidpad track_style:=skidpad \
  perception:=sim estimator:=truth enable_metrics:=false
```

The view defaults to 15 m / 240 degrees, sim cone accumulation is off, and
only one planner owns the path. After Go, the car creeps until colored lane
and timing-gate observations validate entry, stops for acquisition, then
drives two clockwise right laps and two counterclockwise left laps. It exits
and stops, then the mission reaches `FINISHED`. RViz shows the visible cones,
fitted circles and the short active path sent to pure pursuit.

The large orange pairs now bracket the shared center timing line at y=0.
Their +/-0.5 m offsets and small-cone lane spacing are simulation placement
choices; the center timing line itself comes from D.10.1.3. These pairs no
longer mark illustrative gates at y=+/-7 m.
The fitter now initializes from detected cone chords and the known center
separation, rather than assuming a center from the staging distance. It uses
the initial vehicle heading to resolve orientation. Acquisition reports include
the number of retained center candidates and refined pose hypotheses.

`perception:=lidar` applies the same planner view filter and detector range;
there is no orange classification in that pipeline, so acquisition cannot
creep or authorize event laps without independently verified orange and
blue/yellow classifications. The demo above uses
ideal labelled detections and truth odometry; it is not the no-shortcuts gate.

All [acquisition thresholds](../lhr_track_builder/README.md#skidpad-planning)
are launch arguments with matching defaults/types. `fov_deg` / `max_range_m`
retain 200 / 20 defaults for other missions. `accumulate_detections` defaults
to true elsewhere; skidpad defaults to false. `path_closed` and
`require_planner_limit` default to false/true for skidpad, true/false otherwise;
`planner_timeout_sec` is 0.5. Do not disable the required cap or enable closed
paths for acquisition. Chaining parameters apply only to the autocross planner.


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
