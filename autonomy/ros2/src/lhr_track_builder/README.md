# Track Builder

`lhr_track_builder` turns detected cone markers into an ordered centerline on
`/lhr/track/centerline`. It also publishes green centerline visualization
markers on `/lhr/track/centerline_markers`.

## Pairing

The `pairing_strategy` parameter selects one of three algorithms:

- `nearest` pairs each blue (`left_cones`) cone with the nearest unused yellow
  (`right_cones`) cone. This strategy requires classified cones.
- `classified` computes a minimum-cost one-to-one left/right assignment within
  `track_width +/- track_width_tolerance`, leaving unmatched cones unused.
  The Gazebo launch selects it with
  `perception:=lidar lidar_classify_sides:=true`. It depends on correct side
  labels; sparse detections can cause the experimental classifier to mislabel
  a boundary and leave gaps or an incorrect path.
- `boundary` (default) Delaunay-triangulates all cone positions without considering
  color, extracts the unique triangle edges, then rejects blue-blue and
  yellow-yellow edges. Blue-yellow edges and edges involving an unknown-color
  (`cones`) marker are retained only when their length is within
  `track_width +/- track_width_tolerance`. Accepted-edge midpoints become
  centerline candidates.

To use the original geometry-only Delaunay pairing, set
`pairing_strategy:=boundary` and `boundary_use_color:=false`. This disables
same-color rejection and filters edges only by track width, even when colors
are available. The default `boundary_use_color:=true` retains color-aware
filtering. Edges involving unknown-color cones are accepted by geometry in
either mode.

Classification comes from marker namespaces (`left_cones`, `right_cones`,
and unclassified `cones`), not the displayed marker RGB color. Receiving
unclassified cones emits a warning at most once every five seconds. With
`boundary`, the message explains that geometry is used for those cones; with
`nearest` or `classified`, it explains that they are ignored and suggests
selecting `boundary`.
Deletion markers are ignored when reading the current cone map.
The warning does not interrupt processing or discard stored detections.

Delaunay triangulation can fail for fewer than three points or degenerate
layouts such as collinear cones; the node publishes no new path for those
inputs.

All candidate midpoints are ordered with the configured `chaining_alg` before
the published path is capped by `max_points`. With odometry, chaining starts
at the midpoint nearest the vehicle, even when it lies beyond the first
`max_points` candidates. Without odometry, it starts at midpoint zero.

## Chaining

- `greedy` visits every midpoint using nearest-neighbor steps and
  reverses the result if its initial direction opposes the vehicle heading.
- `constrained_greedy` (default) searches a sector centered on the last accepted midpoint.
  The sector initially follows vehicle yaw, then follows each accepted segment.
  Without odometry, the nearest distinct midpoint at least `min_step_m` away
  supplies the initial heading; this cannot establish the car's forward direction.
  Candidates outside the step limits or sector half-angle are rejected. Among
  eligible points, the lowest score wins:

  ```text
  distance_weight * distance / max_step_m
    + heading_weight * (heading_error / radians(max_turn_deg)) ** 2
  ```

  Exact score ties use input index. Coincident points and points closer than
  `min_step_m` to an accepted point are permanently suppressed. Chaining stops
  when no candidate qualifies, returning the valid prefix without reversing it
  or reconnecting leftover points. The constraints also apply to two-point inputs.
- `fixed_width_beam_search` uses the same initial heading, moving sector,
  duplicate suppression, and step score, but retains up to `beam_width` partial
  paths at each depth. Each path has its own heading and remaining candidates.
  All valid extensions compete globally by accumulated step cost; multiple
  survivors can come from the same parent. Exact cost ties use the sequence of
  input indices. Dead ends are discarded when other paths can advance. If none
  can advance, the best current prefix is returned. Otherwise, after
  `beam_depth` expansions, the lowest-cost surviving path is returned without
  reversing it. Constraints also apply to two-point inputs.

Shared constrained-greedy and beam-search parameters are read at startup:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `min_step_m` | `0.3` | Minimum midpoint separation (m); zero still suppresses exact duplicates |
| `max_step_m` | `4.0` | Maximum step distance (m) |
| `max_turn_deg` | `70.0` | Sector half-angle relative to the current heading (degrees) |
| `distance_weight` | `1.0` | Preference for nearer candidates |
| `heading_weight` | `1.0` | Preference for smaller heading changes |

All values must be finite, with `0 <= min_step_m < max_step_m`,
`0 < max_turn_deg <= 180`, and nonnegative weights with at least one positive.
Defaults are starting values for simulation tuning, not validated vehicle limits.
Increase `heading_weight` to discourage zigzags; reduce it if the path skips
nearby corner points. Reduce `max_step_m` to discourage jumps across track
sections; increase it if legitimate gaps terminate the path. The sector does
not enforce curvature, boundary clearance, or self-intersection constraints.

Beam search adds two startup parameters, both positive integers:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `beam_width` | `3` | Maximum number of partial paths retained globally per depth |
| `beam_depth` | `10` | Maximum number of midpoint additions after the starting point |

The default beam depth returns at most 11 midpoints, including the start.
This is a single bounded search; it does not repeatedly search and commit a
point to produce a longer path. Costs are compared only at equal depths.
Width 1 follows constrained greedy's choices up to the depth limit. A wider
beam preserves more alternatives, but requires more work and still cannot
recover paths discarded at an earlier depth. Increase depth for a longer
published prefix. Neither setting guarantees a globally optimal path.

For example, after building and sourcing the workspace:

```bash
ros2 run lhr_track_builder track_builder --ros-args \
  -p chaining_alg:=constrained_greedy \
  -p max_step_m:=4.0 -p max_turn_deg:=70.0 \
  -p distance_weight:=1.0 -p heading_weight:=1.0
```

To select beam search:

```bash
ros2 run lhr_track_builder track_builder --ros-args \
  -p chaining_alg:=fixed_width_beam_search \
  -p beam_width:=3 -p beam_depth:=10
```

The same eight chaining parameters can be passed directly to the Gazebo demo
in either perception mode. For example:

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py perception:=lidar \
  chaining_alg:=fixed_width_beam_search beam_width:=5 beam_depth:=20
```

See the [Gazebo launch reference](../../README.md#running) for a full tuning
example. Values take effect at startup; `greedy` ignores constrained
step/score settings, and beam width/depth affect only beam search.

Functional tests cover direction, moving sectors, scoring, gap termination,
duplicates, pairing integration, path caps after vehicle-relative chaining,
global beam pruning, accumulated path costs,
depth limits, alternatives to greedy dead ends, geometry-only pairing,
classified pairing, deletion-marker handling, and unclassified-cone warnings.
Run them in the ROS environment with
`colcon test --packages-select lhr_track_builder` from the workspace root.

See the [ROS 2 workspace reference](../../README.md#lhr_track_builder-track_builder)
for topics and all parameters.
