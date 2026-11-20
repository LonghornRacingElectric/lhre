# Gazebo worlds

Committed SDF scenes use the same ordered cone pairs as
[lhr_trackgen](../../lhr_trackgen/README.md). Launch finds
`<track_style>_seed<seed>.sdf` in this directory or the installed package;
fixed competition imports use `<layout_id>.sdf` with a verified reference sidecar.
An explicit `world:=...` overrides that lookup; metrics and the viewer read
that actual file. Generated courses are synthetic; see the
[competition imports](competition/README.md) for the preserved AMZ layouts.

## Extreme autocross set

These seven fixed courses exercise DD.4.4.1 edges from
[the track rules reference](../../../../docs/rules/fsae-track-and-events.md).
Every named style has **seed 1 only**. Each saved world passes the shared
rules-envelope check, including segment crossings and return-lane separation.

| Style | Limit or feature | Measured reference | Stack behavior stressed |
| --- | --- | --- | --- |
| `extreme_narrow` | 3 m minimum clear width | 226.607 m lap; minimum clear width 3.004 m | Side classification (#85), pairing, lateral clearance |
| `extreme_hairpin` | 80 m straight directly into a 9 m turning-diameter half-turn | 201.460 m lap; 80.000 m straight; 4.500 m radius | Speed-profile braking, pursuit lookahead and steering |
| `extreme_decreasing` | Turn tightens from roughly 9 m radius toward a 4.5 m design apex | 205.934 m lap; sampled minimum radius 4.687 m | Curvature preview, speed reduction and steering response |
| `extreme_slalom_chicane` | Alternating slalom and a separate chicane | 259.213 m lap; minimum radius 4.897 m | Side classifier, rapid curvature reversals, track chaining |
| `extreme_doubleback` | Opposite-direction lanes 9 m apart | 218.309 m lap; 4.500 m turns | Cross-lane association, planner shortcuts and reference progress |
| `extreme_500m` | Maximum lap length | 499.99999 m reference lap | Mapping, accumulated EKF error and lap bookkeeping |
| `extreme_combined` | 3 m minimum corridor, 80 m approach, 9 m turning diameter, slalom/chicane and nearby return lane | 328.988 m lap; minimum clear width 3.007 m | Interaction of speed planning, pursuit, pairing and limited clearance |

Clear width uses the square cone bases' circumscribed diameter, matching the
conservative checker from #211. Physical cones are sampled along boundary
arc length independently of the dense construction geometry. Boundary gaps
stay below 2 m and above 0.5 m, including tight inside turns. A chord reserve based on a fixed half-angle per course keeps the narrowest clearance within 1 cm of 3 m; cone-pair
distance includes that reserve. Turning diameter refers to the centerline. A legal
layout does not establish vehicle drivability or full event compliance:
staging, timing gates and exit lanes are not supplied by these fixtures.

The 500 m target is the closed polyline metrics scores. Its 10 micrometre
reserve keeps serialization inside the upper limit. Extreme worlds store XY
at 10 decimal places, with negative zero normalized, so the strict radius
regressions survive serialization without twelve-decimal platform noise.
Other styles retain their existing precision. Fixtures stage before the first
feature, preserving the complete hairpin and slalom approaches.

## Regenerate and run

From `autonomy/ros2`, in the ROS environment:

```bash
python3 src/lhr_gazebo/scripts/generate_world.py --style extreme_hairpin
ros2 launch lhr_gazebo gazebo_demo.launch.py track_style:=extreme_hairpin \
  seed:=1 perception:=lidar estimator:=ekf gui:=false rviz:=false
```

Repeat the generator command for each style in the table to regenerate the
set, then rebuild `lhr_gazebo` so installed scenes agree. The generator checks
coordinates at their saved precision before writing. `colcon test
--packages-select lhr_trackgen lhr_gazebo` also checks exact limit dimensions,
world freshness, matching simplified cone publication, launch parameter
startup, and complete progress around the saved references.

Fixed styles ignore generic `width_m`, `cone_spacing_m`, radius, jitter and
waypoint flags; their named dimensions and boundary cone sampling are part
of the fixture. Seed values other than 1 fail. Both perception branches use
the corresponding cone-center pairing width. Metrics remains on the
`autocross` mission and scores the selected world's ordered reference;
perception and control receive no route from that reference.

To run just this set with course-derived reference laps:

```bash
SEEDS= EXTREMES=all EXTREME_REPEATS=1 ACCEL_REPEATS=0 SKIDPAD_REPEATS=0 \
  RUN_S=900 OUT="$HOME/lhr-test-data/extreme-autocross" \
  ../testing/tools/gate/run_gate.sh
```

`EXTREMES` also accepts a space-separated subset of style names. See the
[gate tools](../../../../testing/tools/gate/README.md). The extension uses
existing scorer thresholds and does not alter the approved MVP defaults.
The [Sim v2 criteria](../../../../docs/plans/sim-v2-pass-criteria.md) include
these scenarios. Each row selects the current Mid-360 and camera-fusion stack.
`BASELINE=1` adds optional truth-stack comparisons; lap scoring uses the course
reference. `RUN_S` counts simulation seconds. Geometry acceptance is
not a claim that any course has passed the driving gate.

Recorded driving results live in the
[HIL campaign](../../../../testing/2026-11-19-extreme-hil/README.md).
