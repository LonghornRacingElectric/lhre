# lhr_perception

LiDAR cone detection and camera color segmentation for the Gazebo autonomy
pipeline. The LiDAR node turns
`/lhr/lidar/points` into a persistent cone map on
`/lhr/sensor/cones_detected`. It also publishes experimental geometry-inferred
track sides for `lhr_track_builder`.

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
launch. Add an RViz Image display for `/lhr/perception/cone_colors/debug_image`
with Reliability **Best Effort** to inspect the labels. For example, increase
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

## Pipeline

`lidar_cone_detector` removes the ground and vehicle body, applies a range
filter, clusters cone-sized returns, transforms the cluster centroids into the
map frame, and associates them with previously observed cones.

The detector retains a short odometry history and interpolates the vehicle
pose at each pointcloud timestamp. A scan waits for the odometry sample after
its timestamp instead of using the newest callback pose. This matters most in
sharp turns, where a small time offset can move a distant cone far enough to
create a duplicate map entry. A LiDAR scan that arrives before the first
odometry sample is discarded once during startup; this is expected and is
reported as an informational message.

Association considers every detection and existing cone inside
`dedup_radius`, resolves the closest pairs first, and uses each cone at most
once per scan. Matched positions are updated with a running average. The
detector then accumulates a side vote from each cone's lateral position in a
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
mapped cone in the orange `cones` namespace. Timestamp interpolation,
clustering, one-to-one scan association, and position averaging remain active.
The setting is read at startup; restart the node to change it.

## Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `max_range` | `20.0` | Furthest accepted return in metres |
| `min_range` | `0.9` | Closest accepted return in metres |
| `ground_z_min` / `ground_z_max` | Vehicle-derived / `0.5` | Sensor-frame height band retained after ground removal |
| `cluster_radius` | `0.35` | Euclidean clustering radius in metres |
| `min_cluster_points` / `max_cluster_points` | `1` / `50` | Accepted cluster size |
| `max_cluster_extent` | `0.5` | Largest accepted cluster extent in either horizontal axis |
| `dedup_radius` | `1.5` | Maximum cone association distance in metres |
| `publish_hz` | `10.0` | Pointcloud processing rate |
| `pose_history_sec` | `2.0` | Odometry retained for timestamp interpolation |
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

The Gazebo launch overrides several clustering defaults for its simulated
VLP-16. Keep the launch values and these defaults separate: the defaults are
the node contract, while the launch file is sensor-specific tuning.

The [Mid-360 acceptance results](../../../testing/2026-10-04-mid360-acceptance/README.md)
show sparse cone returns in daylight and a blind zone close to the sensor.
The current per-scan clustering has not been retuned or validated on that
sensor's recordings; the real sensor will need frame stacking with odometry.

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
