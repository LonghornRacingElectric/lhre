# lhr_perception

LiDAR cone detection for the Gazebo and on-car autonomy pipeline. The node
turns `/lhr/lidar/points` into a persistent cone map on
`/lhr/sensor/cones_detected`, with geometry-inferred left and right track
boundaries for `lhr_track_builder`.

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
and are immutable once accepted. Repeated scans from one tight-corner viewpoint
therefore cannot merge or relabel the two track sides.

LiDAR still does not observe cone colour. These labels describe the inferred
track side, not a measured blue or yellow cone. A cone stays in the `cones`
namespace until there is evidence, then moves to `left_cones` or
`right_cones`. Namespace changes include a marker deletion so RViz does not
leave a stale orange copy behind.

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

## Verification

Unit tests cover pose interpolation through angle wrap, simulation clock
resets, sharp-turn reprojection, nearest-cone selection, one-to-one scan
association, side voting, label locking, and rejection of cross-track links.
The Gazebo package also replays selected generated autocross tracks as
progressively discovered maps through side inference and one-to-one centerline
pairing with injected pose error.

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
