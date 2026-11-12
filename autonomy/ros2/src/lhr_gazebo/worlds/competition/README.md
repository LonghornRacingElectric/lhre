# Imported competition layouts

Fixed FSG and FSI cone layouts from [AMZ FSSIM](https://github.com/AMZ-Racing/fssim),
kept at their original metre coordinates. These are competition-named simulator
layouts, **not certified surveyed ground truth**. The source README reports
FSG 2018 trackdrive validation, but does not date or certify these individual
coordinate files. No event year is inferred from that statement.

## Provenance and edits

Both YAML files are copied verbatim from AMZ revision
`cf652d8d3f1e13031dad3fb75eb3d4e6fbaaeff4`:

| Layout | Source | Physical cones after normalization |
| --- | --- | --- |
| `amz_fsg` | [FSG.yaml](https://github.com/AMZ-Racing/fssim/blob/cf652d8d3f1e13031dad3fb75eb3d4e6fbaaeff4/fssim_gazebo/models/track/tracks_yaml/FSG.yaml) | 94 blue, 88 yellow, 4 large orange |
| `amz_fsi` | [FSI.yaml](https://github.com/AMZ-Racing/fssim/blob/cf652d8d3f1e13031dad3fb75eb3d4e6fbaaeff4/fssim_gazebo/models/track/tracks_yaml/FSI.yaml) | 80 blue, 74 yellow, 4 large orange |

AMZ licenses the data under [MIT](https://github.com/AMZ-Racing/fssim/blob/cf652d8d3f1e13031dad3fb75eb3d4e6fbaaeff4/LICENSE.md),
copyright 2019 Akademischer Motorsportverein Zürich. The original notice is
retained in [LICENSE.md](LICENSE.md). Machine-readable attribution and edits
are in [provenance.json](https://github.com/LonghornRacingElectric/lhre/blob/main/autonomy/ros2/src/lhr_gazebo/worlds/competition/provenance.json).

The source YAML specifies sides, not color IDs. Normalization maps left to
blue and right to yellow for our driving direction; AMZ's legacy SDF uses a
different side/color assignment. Orange categories map directly. Exact repeated
closing vertices are removed (FSG: both sides; FSI: right side), so one cone is
spawned per physical location. CSV rows retain per-side source ordering.
Coordinates are rounded to 12 decimals for SDF serialization. There is no
rescaling, recentering, widening or moving cones to pass rules checks.

The source spawn is at the front wing. The importer moves only the vehicle's
requested spawn backward by the canonical car's nose-to-rear-axle distance.
The original FSI pose does not fit our full vehicle envelope. Bundled worlds
explicitly use `--fit-spawn`: select the nearest reference vertex within 5 m
and align with its tangent, requiring the whole canonical footprint to clear
road edges and cones with 5 cm reserve. Both the requested alignment pose and
executed spawn are recorded. Without that option an unsafe spawn is rejected.
Map axes and all cone coordinates are preserved. Cones use this repo's existing
FSAE cone models and collision envelopes, rather than AMZ meshes.

## Rebuild

From `autonomy/ros2`, with the ROS workspace and SciPy installed:

```bash
for name in amz_fsg amz_fsi; do
  python3 src/lhr_gazebo/scripts/import_world.py \
    --source src/lhr_gazebo/worlds/competition/$name.yaml --format fssim \
    --normalized-csv src/lhr_gazebo/worlds/competition/$name.csv \
    --name $name --provenance src/lhr_gazebo/worlds/competition/provenance.json \
    --output src/lhr_gazebo/worlds/$name.sdf --allow-out-of-envelope --fit-spawn
done
./scripts/build.sh
```

For another layout, supply a CSV with exactly `x,y,color` and a rear-axle
`--spawn X Y YAW` (metres, radians). Allowed colors are `blue`, `yellow`,
`orange_small`, `orange_large`. Blue and yellow rows must each follow a closed
boundary in order, with at least six vertices and no repeated closing row.
Unknown colors, duplicate positions and nonfinite coordinates are rejected.
Use `--provenance` to retain source and licensing information. This importer
handles closed autocross/trackdrive circuits, not branching routes or skidpads.

Each world has a `.reference.json` sidecar. Unequal boundaries are aligned
monotonically by repeating scoring indices, preserving every physical boundary
segment. Gate midpoints are resampled at 0.5 m and smoothed with a fixed 2 m
periodic Gaussian. Every reference chord is checked inside the source road.
This inferred route supplies metrics, viewer truth and the optional offline
spawn alignment, never a runtime planner input. Keep both files together: their
hashes bind the reference, audit and exact world bytes; missing or edited files fail before launch.

## Envelope flags and running

The shared [rules checker](../../../lhr_trackgen/README.md#rules-compliant-autocross)
measures length, segment straightness and circumradius on the unsmoothed
paired gate midpoints, including closure. Smoothing applies only to the scoring
route; it cannot erase a long straight or soften an audited corner. Cone-base
clearance uses the original boundary segments.
Self-intersections and nearby remote boundaries are also flagged. This is a
geometric simulator audit, not full sporting-rule or physical validation.
Radius and straightness describe the inferred gate polygon, not an official
racing line. Only collinear segments count as one straight; near-straight
sections with small bends are not combined. Maximum original cone spacing is recorded as a diagnostic.

| Layout | Gate-polygon length | Minimum gate-polygon radius | Minimum clear width | Flags |
| --- | --- | --- | --- | --- |
| `amz_fsg` | 308.84 m | 3.46 m | 2.95 m | radius <4.5 m, width <3 m, remote boundary crowding |
| `amz_fsi` | 217.58 m | 2.50 m | 2.82 m | radius <4.5 m, width <3 m |

Generation requires `--allow-out-of-envelope` for these retained inputs.
Launch independently requires acknowledgement and always logs the violations:

```bash
ros2 launch lhr_gazebo gazebo_demo.launch.py \
  track_style:=amz_fsg seed:=1 allow_noncompliant_track:=true \
  perception:=lidar estimator:=ekf gui:=false rviz:=false max_step_m:=8
```

Both layouts are fixed (`seed:=1` only). The simplified `perception:=sim`
branch observes the physical source cones through `sensor_sim`; the LiDAR
branch observes the rendered scene or Mid-360 caster. Neither branch receives
the scoring centerline. Pairing uses the imported median gate width instead
of the procedural generator's width. Other generator knobs do not edit imports.

The [gate runner](../../../../../testing/tools/gate/README.md#imported-competition-layouts)
can append these as diagnostic scenarios. The [Sim v2 criteria](../../../../../docs/plans/sim-v2-pass-criteria.md)
include these layouts. Clean laps at default planner settings remain tracked
in [#225](https://github.com/LonghornRacingElectric/lhre/issues/225).
The runner keeps imports opt-in and uses the current Mid-360/fusion stack.
Course-derived lap times use the same smoothed reference as metrics;
truth-stack baselines remain optional comparisons.

FSG's sparse source gates exceed the default planner `max_step_m:=4`;
`max_step_m:=8` above is an explicit diagnostic override, not a new planner
default or a gate pass. FSI can also use the same override for a combined
campaign. Preserving the original cones deliberately exposes these limits.

## Verification

On 2026-11-07, the affected trackgen, metrics and Gazebo checks reported
953 tests, no failures and 3 skips; the strict docs build passed. Regression
coverage checks exact source-cone preservation, unequal boundary alignment,
reference containment, full directed circuit progress, spawn clearance,
world/sidecar binding, typed launch parameters in both perception/estimator
branches, and gate planning/scoring with layout-specific baselines.

Four isolated 30-wall-second Gazebo Harmonic smoke runs used the canonical
Gazebo plant, fitted spawns, `ready_hold_sec:=0`, `max_step_m:=8`, truth
odometry and no safety monitor. Both layouts produced odometry, raw clouds,
cone detections and forward planner paths in both perception modes. The FSG
LiDAR run was correctly invalidated as off course after 8.95 m of scored
travel (4.38 simulation seconds); the other three had no scoring/node failure
within the short observation window. This establishes startup integration,
not a completed lap, no-shortcuts EKF/safety gate pass or physical validation.
The FSG driving failure remains visible for planner/perception studies; no
controller defaults or scoring thresholds were relaxed to hide it.
