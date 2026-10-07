# lhr_track_builder

Builds `/lhr/track/centerline` from the persistent cone map on
`/lhr/sensor/cones_detected`. The output path is consumed by control, mission
management, and metrics.

## Pairing strategies

- `index` pairs generator IDs and is the deterministic simulation default.
- `nearest` greedily pairs classified cones and is retained for experiments.
- `classified` computes a minimum-cost one-to-one assignment between inferred
  left and right cones. Pairs outside the configured track-width band remain
  unmatched. This is the current LiDAR launch setting. It depends on correct
  side labels; the geometry-only classifier can mislabel a sparse boundary.
- `boundary` uses width-filtered Delaunay edges when no side information is
  available. It can create several candidates from one cone and is retained as
  a fallback.

The selected midpoints are ordered from the vehicle pose with a nearest-point
walk. Partial maps can still need a path-continuity planner. One-to-one
pairing removes duplicate midpoint candidates when the side labels are
correct, but wrong labels can create gaps or an incorrect path.

## Parameters

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `frame_id` | `map` | Output path frame |
| `publish_hz` | `5.0` | Centerline update rate |
| `max_points` | `200` | Maximum published path points |
| `pairing_strategy` | `index` | Pairing method listed above |
| `cone_topic` | `/lhr/sensor/cones_detected` | Input marker array |
| `track_width` | `3.5` | Expected cross-track cone spacing |
| `track_width_tolerance` | `1.0` | Allowed width error |

## Verification

```bash
cd autonomy/ros2
colcon test --packages-select lhr_track_builder
colcon test-result --verbose
```
