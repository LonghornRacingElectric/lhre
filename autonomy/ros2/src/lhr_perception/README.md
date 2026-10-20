# lhr_perception

Cone detection from `/lhr/lidar/points`, shared by the kinematic Mid-360
simulation and Gazebo. The detector subscribes to clouds and stamped
odometry; it never subscribes to ground-truth cones.

## Coordinates and timing

Each cloud needs a nonzero timestamp and a frame ID. The detector looks
up `base_link <- cloud frame` and `map <- base_link` at that timestamp.
The full stamped odometry pose is also inserted into the TF buffer, so
Gazebo's odometry bridge does not need to provide a duplicate map transform.
The sensor mount must be published through TF; no mount position or
orientation is guessed inside the detector.

Ground removal and vehicle exclusion operate in `base_link`, whose origin
is at ground level. `ground_z_min` now means height above that origin,
**not sensor-frame z**. Existing sensor-frame overrides must be updated.
Changing sensor roll, pitch, yaw, or height therefore changes visibility
without changing the ground threshold. Each filtered scan is transformed into `map` at its own timestamp, including
all three rotation axes, before clustering the accumulated points.

If TF arrives after a cloud, processing retries without blocking ROS
callbacks. After `transform_wait_sec` the cloud is discarded with a warning;
there is no fallback to the latest vehicle pose. Newer clouds replace pending
ones. Nonfinite returns and clouds without a frame or timestamp are rejected.
The cloud subscription accepts best-effort sensor publishers as well as
reliable publishers.

## Pipeline and contract

Base-frame height filter → car exclusion → horizontal sensor-range filter →
scan-time map registration → bounded cloud history → XY connected-component
clustering → cone-size checks → persistent map.

`/lhr/sensor/cones_detected` is a reliable, transient-local `MarkerArray`.
Markers use frame `map`, namespace `cones`, and IDs assigned by the detector.
They have no colour or left/right classification. Use the track builder's
`boundary` strategy, which constructs a centerline from geometric cone pairs.
`/lhr/perception/debug` shows the accumulated detections.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `ground_z_min` | `0.05` | Minimum height in base_link, metres |
| `ground_z_max` | `0.55` | Maximum height in base_link, metres |
| `min_range` | `0.9` | Minimum horizontal distance from sensor, metres |
| `max_range` | `20.0` | Maximum horizontal distance from sensor, metres |
| `cluster_radius` | `0.35` | XY neighbourhood radius, metres |
| `min_cluster_points` | `3` | Minimum points across the retained scans |
| `max_cluster_points` | `500` | Maximum cluster size |
| `max_cluster_extent` | `0.5` | Maximum extent along either map-frame XY axis |
| `dedup_radius` | `0.6` | Merge radius in map, metres |
| `publish_hz` | `10.0` | Processing timer frequency |
| `stack_window_sec` | `0.5` | Scan-time history window; zero uses only the current scan |
| `stack_max_frames` | `10` | Bound retained scans even at high input rates |
| `transform_wait_sec` | `0.5` | Maximum wait for a pending cloud's transforms |

Use `ros2 launch lhr_demo mvs_demo.launch.py perception:=lidar` to exercise
this path without Gazebo. See [demo launch](../lhr_demo/README.md) and
[tests](test/README.md).

## Limits

Height filtering assumes a locally flat ground plane in the vehicle frame;
it is not terrain estimation. Three-point clusters can still mistake compact clutter for cones. A 5 cm
cutoff retains more low returns but also admits more ground noise; this is
not a validated terrain filter. Persistent mapping has no aging or correction for localization
drift. Boundary planning is heuristic, particularly on complex autocross
tracks; this mode is not a claim of validated driving performance.

## Hardware evidence

The [October 4 Mid-360 acceptance test](https://github.com/LonghornRacingElectric/lhre/blob/52fb09e6a71e788323171ac86d29c1fae98e08c3/autonomy/testing/2026-10-04-mid360-acceptance/README.md)
reports intermittent daylight small-cone detections beyond roughly 8 m and
that the 15 cm height cutoff discards many real returns. The updated defaults use a 5 cm cutoff and three-point evidence over a
0.5 second history. These settings have now been evaluated on stationary acceptance captures;
clutter precision and performance during motion remain unvalidated. The recording evaluator below measures stationary reference matches;
clutter annotations and moving bags are still required for full validation. See [acceptance comparison](../lhr_lidar_sim/README.md#acceptance-comparison).

Each scan is registered before accumulation; duplicating one stamped scan
cannot satisfy the point threshold. Empty scans expire old evidence. A
backward scan timestamp clears history and the persistent map for replay
restarts. Stacking compensates motion between scans, but does not deskew
individual points within a scan. Accuracy depends on odometry and TF;
current kinematic odometry is ground truth, not a noisy state estimator.

## Recording evaluation

`evaluate_recordings` replays complete 100 ms frames from the acceptance
NPZs through the production detector. Each scan uses its recorded points;
mean IMU gravity supplies the stationary mount rotation, the session height
supplies mount z, and odometry is stationary. It defaults to ROS domain 64
to isolate its publications; choose another unused `--domain-id` if needed.

```bash
ros2 run lhr_perception evaluate_recordings \
  --raw-dir data/2026-10-04-mid360-acceptance/raw \
  --results src/lhr_lidar_sim/lhr_lidar_sim/patterns/acceptance_results.csv \
  --output data/detector-recordings.json
```

Use repeatable `--run` arguments to select captures, or `--setting stack_3`
to evaluate only the current defaults. The comparison settings are:

| Name | Ground cutoff | Minimum points | History |
| --- | --- | --- | --- |
| `legacy_evidence` | 15 cm | 1 | Single scan |
| `stack_3` | 5 cm | 3 | 0.5 s |
| `stack_5` | 5 cm | 5 | 0.5 s |

`legacy_evidence` isolates the former height/evidence settings; it retains
the current coordinate handling and connected-component clustering. This
is not a replay of an old software revision. Dense recorded clutter is
clustered using a sparse neighbor graph to avoid per-point Python queries.

The evaluator counts matches within 35 cm of the published reference cone
centroid **before persistent-map merging**. Empty frames and startup remain
in the denominator. It reports reference-match rates and centroid errors,
plus unmatched candidates. Other objects are not exhaustively annotated;
unmatched candidates must not be called proven false positives. Hashes of
the captures and vehicle config accompany the results.

On the representative 10.3 m outdoor run 2, the legacy evidence settings
matched in 26% of frames, `stack_3` in 84%, and `stack_5` in 45%. Median
matched centroid error for `stack_3` was 1.5 cm against the published
centroid. This supports the three-point setting for this experiment;
it does not establish safety or detection performance while driving.
Moving recordings and clutter annotations remain validation work.

Across 21 measured small-cone recordings, the current defaults matched
the outdoor reference in 100% of frames through 7.7 m, 93% at 9.0 m,
78–91% across four 10.3 m repeats, 77% at 11.6 m, 27% at 15.3 m and
2.5% at 17.9 m. These are per-window reference-match rates in the recorded
scenes, not persistent-map coverage. Captures without a measured reference
centroid (including the blind 1.4 m small cone) are excluded from this table.

## Optional side inference

Set `classify_sides:=false` to publish unclassified cones. The node default
is `true`; both demo launchers keep it opt-in with
`lidar_classify_sides:=false`. Classification estimates track sides from
geometry, not measured cone colour. Clustering and one-to-one map association
remain active when classification is off.

The detector accumulates a side vote from each cone's lateral position in a
short local corridor beside the car. Short Delaunay edges share those votes
along a boundary. A slightly longer gap is joined only when it follows the
boundary's local direction. The starting straight seeds the initial labels;
later labels require consistent proposals from poses separated by one metre
and are immutable once accepted. A wrong initial proposal can therefore remain
locked even after better observations arrive.

LiDAR still does not observe cone colour. These labels describe the inferred
track side, not a measured blue or yellow cone. A cone stays in the `cones`
namespace until there is evidence, then moves to `left_cones` or
`right_cones`. Namespace changes include a marker deletion so RViz does not
leave a stale orange copy behind.

Set `classify_sides:=false` on the node to skip side voting and publish every
mapped cone in the orange `cones` namespace. Scan-time TF registration,
clustering, one-to-one scan association, and position averaging remain active.
The setting is read at startup; restart the node to change it.


| Parameter | Default | Purpose |
| --- | --- | --- |
| `classify_sides` | `true` | Infer left/right track sides; false publishes unclassified cones |
| `side_vote_max_range` | `7.0` | Furthest cone used for side evidence |
| `side_vote_max_forward` | `3.0` | Forward extent of the classification corridor |
| `side_vote_max_lateral` | `5.0` | Lateral extent of the classification corridor |
| `side_vote_deadband` | `0.5` | Lateral region that contributes no side vote |
| `side_update_distance` | `1.0` | Vehicle travel required between independent side proposals |
| `side_confirmations` | `2` | Consistent traveled viewpoints required before locking a later label |
| `boundary_link_distance` | `3.0` | Maximum direct same-boundary link length |
| `boundary_gap_distance` | `3.3` | Maximum aligned boundary-gap link length |
| `boundary_gap_angle_deg` | `40.0` | Largest direction change across a boundary gap |


## Verification

Unit tests cover pose interpolation through angle wrap, simulation clock
resets, sharp-turn reprojection, nearest-cone selection, one-to-one scan
association, side voting, label locking, and rejection of cross-track links.
The Gazebo package also replays selected generated autocross tracks as
progressively discovered maps through side inference and one-to-one centerline
pairing with injected pose error. That replay assumes all cones in range are
visible. Live Gazebo scans are sparser: an initial partial map incorrectly
locked several right-side cones as left and the vehicle left the course. Treat
the side-classification tests as algorithm checks, not evidence of autocross
driving reliability.

```bash
cd autonomy/ros2
colcon test --packages-select lhr_perception
colcon test-result --verbose
```

For the full visual check, run the Gazebo autocross demo with LiDAR perception
and ground-truth odometry. Each physical cone should form one stable blue or
yellow map marker after classification. Orange markers may appear briefly
before the car has enough evidence. The green centerline should contain one
point across each cone pair and should not sprout branches as the car yaws:

```bash
./scripts/run_gazebo_demo.sh track_style:=autocross perception:=lidar estimator:=truth
```

Once that baseline is stable, repeat with `estimator:=ekf` to isolate errors
introduced by state estimation.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

## Camera color classification

`camera_color_classifier` reads `/lhr/sensor/zed2i/left/image_rect` and
publishes `/lhr/perception/cone_colors/debug_image` (`sensor_msgs/msg/Image`).
The output is an RGB copy with blue, yellow, and orange region boxes and text
labels. It preserves the input timestamp and optical frame ID and uses
best-effort sensor-data QoS for both input and output. RGB8, BGR8, RGBA8, and
BGRA8 inputs are supported, including row padding. Unsupported encodings or
malformed frames are dropped with a throttled warning.

HSV masks find connected color patches; patches below `min_region_area` are
discarded. These are **candidate color regions**, not validated cone objects:
other colored objects can trigger boxes, occlusion can split a cone, and
touching patches can merge. Orange cone sizes are not distinguished. Neutral
and out-of-range pixels remain unclassified, with no box. No ground-truth
labels, stereo depth, LiDAR association, cone-map labels, or track-builder
changes are involved in this first step.

| Node parameter | Default | Gazebo launch argument |
| --- | --- | --- |
| `min_region_area` | `25` pixels | `cone_color_min_region_area` |
| `blue_hsv` | `[100, 100, 50, 130, 255, 255]` | `cone_color_blue_hsv` |
| `yellow_hsv` | `[20, 100, 50, 35, 255, 255]` | `cone_color_yellow_hsv` |
| `orange_hsv` | `[5, 100, 50, 19, 255, 255]` | `cone_color_orange_hsv` |

Each HSV array is `[h_min, s_min, v_min, h_max, s_max, v_max]`, inclusive.
OpenCV hue uses 0–179; saturation/value use 0–255. Bounds must be ordered
integer ranges and area must be positive. Invalid settings fail startup.
Settings are read at startup; restart to change them. Defaults target the
simulated cone materials and require tuning on physical camera recordings.

From `autonomy/ros2`, after building:

```bash
./scripts/run_gazebo_demo.sh enable_stereo_camera:=true \
  enable_camera_color_classifier:=true
```

The classifier is opt-in, default `false`, under either perception mode and
either estimator. Enabling it without `enable_stereo_camera:=true` fails
launch. The stereo RViz configuration includes the debug Image display. In
a custom configuration, add `/lhr/perception/cone_colors/debug_image` with
Reliability **Best Effort** to inspect the labels. For example, increase
the area cutoff and adjust blue bounds:

```bash
./scripts/run_gazebo_demo.sh enable_stereo_camera:=true \
  enable_camera_color_classifier:=true cone_color_min_region_area:=100 \
  'cone_color_blue_hsv:=[95, 80, 40, 135, 255, 255]'
```

Standalone: `ros2 run lhr_perception camera_color_classifier`. Use standard
ROS topic remapping for other input/output topics. The node needs
`python3-opencv`; rebuild the [Mac Docker image](../../docker/README.md) after
pulling the dependency change.

Tests cover known color patches, neutral pixels, small-region rejection,
threshold/area overrides, supported channel layouts, malformed inputs, debug
annotation, and ROS header preservation. Launch tests evaluate enabled and
disabled settings plus parameter overrides in both perception modes and
estimators and feed the evaluated parameters into node startup. Synthetic
tests do not establish accuracy on live Gazebo frames or physical footage.
