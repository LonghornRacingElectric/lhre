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
