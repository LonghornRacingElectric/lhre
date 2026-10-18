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


## Skidpad acquisition

`mission:=skidpad` in the Gazebo launch replaces this package's autocross
planner with `skidpad_acquisition`. It only acquires the layout and stops;
it does not count or drive event laps. The rigid template's dimensions live
in `lhr_trackgen.skidpad_geometry`, shared with world generation. Inputs are
`/lhr/sensor/cones_detected` and `/lhr/vehicle/odom`, both in `map`.

The planner filters every candidate to a rear-axle-centered radius of 15 m
and a forward 240-degree FOV (including after vehicle motion). Smaller views
are supported. Larger views are rejected. Sim perception additionally disables
accumulation, so its output contains only currently visible cones. LiDAR's map
is filtered again at the planner; this does not change the physical sensor FOV.

Only translation and orientation are fitted. Initial guesses come from cone
chords: two points and a known boundary radius give two candidate centers.
Candidates are scored against both radii, merged within twice the inlier
distance, and paired when their separation is within four times the inlier
distance of the published center separation. The initial vehicle heading
resolves the template's symmetry; template +y points along entry. No staging
translation, world SDF, or generated cone coordinates enter the fitter.

At most 48 spatially spread observations generate chords; all unique input
observations score candidates and participate in refinement. The best bounded
set of rigid-pose hypotheses, including the previous fit when competitive,
is refined using `soft_l1` nonlinear least squares and an analytic Jacobian.
Each solve holds boundary assignments fixed; reassignment occurs between
solves, with at most five rounds. Translation is unrestricted, while yaw stays
within the configured heading prior. Fits on a yaw bound, rank-deficient fits,
insufficient angular/boundary support and comparable conflicting fits are
rejected. Unique cone positions must support both circles and both radii.
Color-side inference is not used as ground truth. Orange lane markers are
excluded from circle fitting.

The phases are `ACQUIRE`, `APPROACH`, `HOLD`, and terminal `ACQUIRED`.
A robust parallel-line fit to the observed small orange lane cones can
supply a finite approach path while the layout fit stabilizes. It requires
three cones per side spanning at least 4 m, width 2.8-3.2 m, heading within
15 degrees of the car, side slopes within 5 degrees, and 0.2 m residuals.
Vehicle footprint clearance and stopping room shorten the supported path.
No orange classification means no approach authorization: today's uncolored
LiDAR pipeline can fit circles but must hold if initial support is insufficient.

Only mission `DRIVING` permits a nonzero speed cap. Creep is bounded by
0.5 m/s and 5 m accumulated odometry travel. A conservative simulation envelope
uses 0.5 m/s² braking, 0.5 s latency and a 0.5 m margin. These are simulation
assumptions, not validated hardware braking performance. Stale inputs,
invalid frames, insufficient corridor support or exhausted travel cause a hold;
odometry jumps latch a fault until node restart. `ACQUIRED` requires a stable
fit and speed below 0.05 m/s for one second. It keeps the command cap at zero.

The fit is evaluated at 5 Hz, counting only new observation messages.
Persistence is a stability check, not independent statistical evidence.
The default ideal sim view already fits from staging, so it need not creep.
All thresholds below are initial simulation values, not measured vehicle limits.
Every parameter is exposed by the Gazebo launch with the same name and type.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `fov_deg` | `240.0` | Forward total view, degrees; maximum 240 |
| `max_range_m` | `15.0` | Maximum view radius, m; maximum 15 |
| `acquisition_speed` | `0.5` | Creep cap, m/s; maximum 0.5 |
| `acquisition_distance` | `5.0` | Total travel budget, m; maximum 5 |
| `acquisition_timeout` | `0.5` | Input receipt freshness, seconds |
| `fit_rms_limit` | `0.10` | Maximum inlier radial RMS, m |
| `fit_inlier_distance` | `0.25` | Inlier radial distance, m |
| `fit_inlier_fraction` | `0.8` | Minimum fraction of circular candidates explained |
| `fit_min_cones_per_circle` | `6` | Unique cones per center; also two per radius |
| `fit_min_arc_deg` | `60.0` | Minimum angular coverage per center |
| `fit_stable_frames` | `5` | Consecutive consistent fits (0.1 m / 1 degree) |
| `fit_min_chord_m` | `2.0` | Minimum cone-pair separation for center proposals, m |
| `fit_max_centers` | `12` | Maximum spatially distinct center candidates, at least 2 |
| `fit_max_hypotheses` | `6` | Maximum rigid-pose hypotheses to refine |
| `fit_heading_tolerance_deg` | `45.0` | Allowed template yaw deviation from entry-heading prior; less than 90 |

Outputs: `/lhr/track/centerline` is only the finite approach path (or one hold
point), `/lhr/planning/speed_limit` is the controller cap, and
`/lhr/skidpad/acquisition` is JSON with phase, fit, support and distance.
`/lhr/skidpad/layout` displays the fitted middle circles; it is not a drive
path. `/lhr/skidpad/visible_cones` displays only the accepted view.

Restart from a fresh simulator state for another trial. There is no automatic
handoff to circle following, no fallback to constrained greedy, and no claim
that acquisition is a completed skidpad mission. Tests cover noisy transformed
fits, missing support, view limits, stale inputs, Go gating, travel limits,
and stopping before terminal acquisition. Chord initialization tests also cover
large unknown translations, yaw wrapping, shuffled observations, outliers,
narrow-arc rejection and the analytic Jacobian against central differences.
