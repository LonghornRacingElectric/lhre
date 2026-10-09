# lhr_track_builder

Build `/lhr/track/centerline` from detected cone markers. `index` pairs the
simplified sensor's left/right IDs; `nearest` pairs classified boundaries;
`boundary` consumes the LiDAR detector's unclassified `cones` namespace.

## LiDAR boundary planning

The boundary strategy constructs a Delaunay graph from observed cones and
selects candidate cone pairs by their lateral width along the route.
Diagonals may span a missing cone; edges aligned with a nearby boundary
tangent or skipping an observed cone are rejected. Cross-track midpoint candidates connect only when their
cone pairs share a triangle; this prevents a global nearest-neighbour tour
from jumping between separate track sections.

Near-vehicle seeds must align with the vehicle heading and have a forward
continuation. Candidate walks respect gap and turn limits; scoring balances
proximity, direction, and connected observed length so a nearby dead end is
not preferred over a useful forward corridor. The resulting path is local
and **open**. There is no connection back to its start and no requirement to
visit every cone in the persistent map. Missing or unusable boundary paths
publish an empty Path, allowing the controller to stop instead of reusing
an old route.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `pairing_strategy` | `index` | `index`, `nearest`, or `boundary` |
| `cone_topic` | `/lhr/sensor/cones_detected` | Detected cone MarkerArray |
| `track_width` | `3.5` | Expected corridor width, metres |
| `track_width_tolerance` | `1.0` | Cross-track pair width tolerance, metres |
| `planning_horizon_m` | `30.0` | Maximum connected local path length |
| `max_path_gap_m` | `4.0` | Maximum step between connected midpoint candidates |
| `max_path_turn_deg` | `60.0` | Maximum heading change per step |
| `max_points` | `200` | Maximum path points |
| `publish_hz` | `5.0` | Replanning rate |
| `frame_id` | `map` | Output frame |

Geometry and classification remain heuristic: missing cones, clutter, or
ambiguous intersecting corridors can prevent a path. Ground truth does not
enter this planner. The midpoint graph tests cover straight and curved
corridors, shuffled IDs, and disconnected sections.
