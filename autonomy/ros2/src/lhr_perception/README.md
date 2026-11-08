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
orientation is guessed in the default `sensor_transform_source=tf` mode.
Gazebo explicitly selects `vehicle_config` for its existing level mount;
that mode reads the translation from vehicle YAML and does not require a
sensor TF or a renamed cloud frame. It must not be used for a pitched mount.

Ground removal and vehicle exclusion operate in `base_link`, whose origin
is at ground level. `ground_z_min` now means height above that origin,
**not sensor-frame z**. Gazebo preserves its sensor-frame band by selecting
`height_filter_frame=sensor`; its thresholds are not interchangeable with
base-frame values.
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
Markers use frame `map` and IDs assigned by the detector. LiDAR measures no
colour, so with `classify_sides:=false` every cone stays in namespace
`cones`. The node default, `true`, moves cones with side evidence into
`left_cones` or `right_cones` (see [side inference](#optional-side-inference)).
Both demo launches pass `false` by default. `mvs_demo` then pairs with the
track builder's `boundary_graph` strategy and Gazebo with `boundary`; both
switch to `classified` when sides are on.
`/lhr/perception/debug` shows the accumulated detections.

## Parameters

| Parameter | Default | Meaning |
| --- | --- | --- |
| `sensor_transform_source` | `tf` | Scan-time mount TF; `vehicle_config` selects the level YAML mount |
| `height_filter_frame` | `base_link` | Frame for the height band: `base_link` or `sensor` |
| `ground_z_min` | `0.05` | Minimum height in the selected filter frame, metres |
| `ground_z_max` | `0.55` | Maximum height in the selected filter frame, metres |
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
the default `estimator:=truth` supplies ground truth, and `estimator:=ekf`
registers scans with the EKF estimate instead.

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
boundary's local direction. Links are considered shortest first and cannot
join fragments with opposing accumulated votes of magnitude at least 0.1
on each side, even through an endpoint with no direct vote. Weaker outliers
can still inherit a boundary's majority. This also applies to aligned gaps;
a 3 m link limit cannot silently join the two sides of a 3 m track.
The first pose only anchors the motion check; stationary startup scans do
not seed labels. Side proposals require a fresh scan stamp and at least
`side_update_distance` of travel since the previous viewpoint. A new label
needs `side_confirmations` consistent proposals. Reversing an accepted label
needs twice as many (four by default), so one contradictory glimpse cannot
flip it. Missing or supporting evidence clears a pending reversal.

Each cone's accumulated vote is halved before adding a new direct observation.
Old evidence is bounded and can be corrected; cones outside the current
corridor retain their previous evidence. A connected fragment needs a current
direct side observation to propose any labels, so fresh evidence on a distant
fragment cannot confirm stale votes. Both demo launchers keep geometry-based planning
while sides are unknown, so waiting for independent evidence does not prevent
the initial motion needed to obtain it. Inference-only planning also ignores
side labels when forming geometry, so a bad accepted label cannot stop the
motion needed for correction. Camera fusion retains its color-aware filter.

LiDAR still does not observe cone colour. These labels describe the inferred
track side, not a measured blue or yellow cone. A cone stays in the `cones`
namespace until there is evidence, then moves to `left_cones` or
`right_cones`. A later correction preserves the cone's ID and position and
deletes its previous namespace before adding the replacement, so viewers
and planner snapshots do not retain the old side.

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
| `side_confirmations` | `2` | Consistent traveled viewpoints to acquire a label; reversal requires twice this count |
| `boundary_link_distance` | `3.0` | Maximum direct same-boundary link length |
| `boundary_gap_distance` | `3.3` | Maximum aligned boundary-gap link length |
| `boundary_gap_angle_deg` | `40.0` | Largest direction change across a boundary gap |


## Verification

Unit tests cover pose interpolation through angle wrap, simulation clock
resets, sharp-turn reprojection, nearest-cone selection, one-to-one scan
association, motion-gated side voting, correction hysteresis, namespace
replacement and rejection of cross-track links. The Gazebo package retains
its progressively discovered generated-course replay with injected pose
error. It also exercises the real detector on seeds 1–5 at 3 m width with
25% discovery probability per in-range cone and two independent laps of
viewpoints, then checks side labels and one-to-one centerline pairing.
Another check carries unknown startup and corrected marker snapshots into
the actual geometry planner.

The previous live failure locked sparse initial right-boundary detections as
left. These regressions cover its link and lock mechanisms, but idealized
replay does not reproduce every LiDAR occlusion or prove physical-car
reliability. Classification remains opt-in outside fusion mode.

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

## Camera–LiDAR inspection map

`camera_lidar_fusion` projects the persistent LiDAR map into the raw rectified
left camera image and reuses the HSV region classifier. It publishes a complete
colored `MarkerArray` on `/lhr/perception/cones_fused`, using reliable,
transient-local, depth-1 QoS. The original map remains available. A second output combines camera labels
with inferred LiDAR sides and supplies Gazebo planning in fusion mode (below).
This implements the fusion, fallback and planner-wiring steps of
[#100](https://github.com/LonghornRacingElectric/lhre/issues/100). Live Gazebo
validation and manual visual confirmation are recorded in the
[Gazebo validation notes](../lhr_gazebo/README.md#fusion-planner-validation-2026-11-01).

From `autonomy/ros2`, after rebuilding:

```bash
./scripts/run_gazebo_demo.sh perception:=lidar estimator:=truth \
  enable_stereo_camera:=true enable_camera_lidar_fusion:=true
```

Enable **CameraFusedCones** in the stereo RViz configuration and hide the
original cone display to inspect colors without overlapping markers. Add
`enable_camera_color_classifier:=true` for the independent debug-image view;
fusion works with that view disabled. Both truth and EKF estimation are
supported. Enabling fusion without stereo or with `perception:=sim` fails
launch. Standalone: `ros2 run lhr_perception camera_lidar_fusion`; remap standard
ROS topics for other sources. The package declares both `tf2_ros` and
`tf2_ros_py`; Jazzy supplies the Python TF module through the latter.

Each processed image needs CameraInfo with exactly the same timestamp, optical
frame, and dimensions. Only finite, full-image, undistorted, unbinned rectified
left-pinhole calibration is accepted. Fusion selects the newest map snapshot
at or before the image, within `map_max_age_sec`, and requests map-to-optical
TF at the image timestamp. Missing TF is retried without blocking. There is
no latest-transform or configured-FOV fallback. Histories retain at most two
seconds and 30 maps/60 images/60 calibrations; a timer handles at most one
eligible image per tick. Zero-stamped images are dropped.

Map positions and IDs are preserved. For projection only, the planar map's
z-coordinate is raised by `sample_height_m`, initially half a small simulated
cone's height. This is an assumption, not a LiDAR-measured cone centroid.
Points behind the camera, outside the image, or beyond the 3D range gate are
excluded. Expanded color-region boxes must match one cone each, and each cone
must match exactly one box. Competing matches remain unclassified; boxes are
approximate associations and can still cover unrelated pixels or objects.

The last five accepted votes per ID confirm a color after three agreeing
observations with at least 70% support. Distinct increasing image timestamps
vote once. Conflicting votes can clear or replace a label; missing camera
observations preserve the last label. New cones are gray (`unknown_cones`),
separate from physical orange (`orange_cones`); blue and yellow use
`blue_cones` and `yellow_cones`. LiDAR display colors/namespaces never count as
camera observations. All cones publish even without camera data.

Input is a complete map snapshot in `map`, with ADD markers in `cones`,
`left_cones`, or `right_cones`. Namespace DELETEs beside an ADD are transitions;
absence from the next snapshot removes an ID. Empty snapshots and DELETEALL
clear state. Mixed frames/timestamps, nonfinite positions, and conflicting ADDs
for an ID are rejected atomically. Label changes/removals delete the old output
namespace. Output ADDs retain the source map timestamp, pose, type, and scale;
the timestamp describes map positions, not camera exposure. Images older than
the last received image are dropped without changing the map, votes, orange
exclusions, or TF history. Map timestamp regression or a backward ROS-clock
jump clears queued inputs and evidence.
An ID moving more than `id_reset_distance_m` from its evidence origin also
starts unknown, and queued old snapshots cannot revive the old evidence.
A restart reusing IDs at almost identical positions cannot be distinguished
from a normal map update by this interface.

| Node parameter | Default | Gazebo argument |
| --- | --- | --- |
| `process_hz` | `10.0` | `fusion_process_hz` |
| `map_max_age_sec` | `0.5` (maximum `2.0`) | `fusion_map_max_age_sec` |
| `camera_max_age_sec` | `0.5` | `fusion_camera_max_age_sec` |
| `sample_height_m` | `0.1625` | `fusion_sample_height_m` |
| `max_color_range_m` | `20.0` | `fusion_max_color_range_m` |
| `pixel_margin` | `6.0` | `fusion_pixel_margin` |
| `vote_window` | `5` | `fusion_vote_window` |
| `min_color_votes` | `3` (at most `vote_window`) | `fusion_min_color_votes` |
| `min_color_fraction` | `0.7` (greater than `0.5`, at most `1.0`) | `fusion_min_color_fraction` |
| `id_reset_distance_m` | `0.5` | `fusion_id_reset_distance_m` |

Height and pixel margin may be zero; other numeric settings must be positive
and finite. Counts are integers. Invalid settings fail startup. HSV bounds
and minimum area reuse the classifier parameters and `cone_color_*` arguments
above, even when its debug node is off. Settings are read at startup.

Pure tests cover projection, axes, mount transforms, pixel/range boundaries,
unique matching, HSV overrides, vote correction, and ID generations. ROS tests
cover stamped TF/retries, calibration pairing, map age, malformed snapshots,
marker transitions, reset, bounded queues, and late subscribers; launch tests
evaluate defaults/overrides through node startup in truth/EKF modes. ROS launch/startup and image-to-planner integration checks run in the
isolated Docker environment. A stationary live-stream smoke check also runs;
see [recorded validation](../lhr_gazebo/README.md#fusion-planner-validation-2026-11-01).
Manual autocross/EKF visuals were confirmed. A later seed-2 live camera
interruption check was reported successful by the operator and passed its
course scoring; see the
[development validation](../../../testing/2026-11-08-side-classifier-fixes/README.md#live-camera-interruption).
No automated timestamp-transition trace or scored camera-classification
accuracy was recorded for that check.
The map does not establish physical-camera accuracy or driving improvement;
planar vehicle TF omits physics roll/pitch. See the
[detailed design](../../../docs/plans/camera-lidar-fusion-design.md) for live
inspection acceptance still to run.

### LiDAR side fallback

The same node publishes `/lhr/perception/cones_classified`, a complete
reliable/transient-local `MarkerArray` using the track builder's side
namespaces. It preserves each source ID, position, pose, scale, type, and map
stamp. In Gazebo fusion mode, the track builder subscribes to this topic.

A confirmed blue camera label becomes `left_cones`; yellow becomes
`right_cones`. Orange keeps its source LiDAR namespace (`left_cones`,
`right_cones`, or unclassified `cones`) in the planner output. Camera evidence can override an
opposite LiDAR side only while the latest processed image uniquely matches
that cone to the same confirmed color and the image is no more than
`camera_max_age_sec` old in ROS time. A missing, ambiguous, or contradictory
match uses the latest LiDAR namespace immediately for boundary colors.
An absent/invalid camera stream or missing calibration/TF uses LiDAR once the previous usable image
expires. The processing timer publishes that transition even with no new map.
Duplicate/older images cannot refresh the timeout. A later matching camera
observation can restore camera priority without discarding the vote history.
Physical orange remains recorded on the inspection map, but it never removes
a cone from geometry-based planning. This preserves acceleration finish gates
and the orange exit lane; both large and small orange cones follow the same
source-side policy, including during camera loss.

LiDAR fallback retains `left_cones`, `right_cones`, or unclassified `cones`
from the source map; it never copies marker RGB as measured camera evidence.
The independent inspection topic keeps its remembered physical colors, so
it may still show blue/yellow while the classified output is using LiDAR.
Each published snapshot begins with DELETEALL for its display, then the
complete current map. A depth-one subscriber can miss a transition without
keeping stale markers. Namespace transitions also delete the old marker on
each output independently.
Removing/resetting an ID also revokes its camera eligibility and physical-color history.

Gazebo enables LiDAR side inference automatically in fusion mode, even with
`lidar_classify_sides:=false`, so the fallback is always available. Run:

```bash
./scripts/run_gazebo_demo.sh perception:=lidar \
  enable_stereo_camera:=true enable_camera_lidar_fusion:=true
```

Enable **ClassifiedCones** in stereo RViz to inspect the planner's input; hide
other cone displays when comparing it with **CameraFusedCones**. Gazebo selects
color-aware `boundary` pairing on this output, preserving geometry for unknown
and orange cones. Without fusion, the original planner topic and
explicit side-classifier switch keep their existing behavior. The geometry
classifier remains experimental and retains its documented failure modes.

`fusion_camera_max_age_sec:=0.9` changes the timeout, forwarded as a double;
the node's `camera_max_age_sec` must be finite and positive. Pure tests cover
camera priority, timeout boundaries, unknown sides, orange, contradictory and
missing matches, duplicate stamps, and ID replacement. ROS tests cover output
namespace transitions and timer-driven expiration. ROS checks run in Docker;
see the [validation record](../lhr_gazebo/README.md#fusion-planner-validation-2026-11-01)
for manual confirmation and remaining live-interruption evidence.
