# lhr_trackgen

Generate seeded cone tracks and publish ground-truth scene markers on
`/lhr/track/cones`. These positions belong to the scene; LiDAR perception
receives only the simulated cloud. Boundary namespaces and IDs remain
`left_cones` and `right_cones` for the simplified detector.

## Competition cones

`cone_geometry.py` defines the nominal envelopes from
[2026 Driverless Specification, Table 1](https://www.formulastudent.de/fileadmin/user_upload/all/2026/rules/FS_Driverless_Specification_2026_v1.1.pdf):

| Kind | Square base | Height | Mass | Stripe |
| --- | --- | --- | --- | --- |
| Blue | 228 mm | 325 mm | 0.45 kg | White |
| Yellow | 228 mm | 325 mm | 0.45 kg | Black |
| Small orange | 228 mm | 325 mm | 0.45 kg | White |
| Large orange | 285 mm | 505 mm | 1.05 kg | Two white |

The displayed markers are colored triangle meshes, with their origin at the
base center on the ground. They replace spheres without changing cone IDs.
The square foot, 32-sided conical body and stripe colors share one definition
with the generated Gazebo assets. The headless ray caster uses the same
height, body radius and square foot, with a continuous circular body.

The rule table specifies the envelope, not the molded profile. The ideal
pointed body, 10 mm base thickness and stripe heights are approximations;
measure a real WEMAS cone before treating the shape as a calibrated target.
Colors do not yet alter simulated LiDAR intensity or reflectivity.

## Optional start/finish gate

Set `start_finish_cones:=true` on the MVS launch to place four large orange
cones around the first centerline pair, in namespace `start_finish`. They
appear in the LiDAR scene with their own dimensions; neither their IDs nor
colors reach the detector. The default is false to preserve existing track
fixtures. It is ignored on `acceleration`, whose layout already has its
gates. This marks a scene gate, not an implemented lap timing system or
an assertion that the generated track is event compliant. Entry/exit layouts
for the closed courses remain future work (acceleration has both); all four
cone asset types are available in Gazebo.

Run `colcon test --packages-select lhr_trackgen` for envelope and marker
contract checks. See [Gazebo](../lhr_gazebo/README.md) for mesh regeneration.

## Track layout

`autocross` and `oval` produce closed, paired boundaries at the requested
width (3.5 m by default). Normals follow the actual curve. Cone spacing is
an upper bound on either boundary, including the closing gap (default 2 m);
turns receive extra pairs so the outside boundary stays well marked. Width
jitter is removed. These dimensions describe cone centers, not clear space
between the square feet.

Autocross reduces the seeded radial and angular perturbations until the
centerline bend radius is at least the larger of 6 m and twice the width,
and neither boundary crosses itself or the other boundary. `jitter_m` is
therefore an upper bound. Impossible dimensions fail rather than publishing
a folded track. Seeds remain reproducible, but layouts change from older
versions; regenerate Gazebo worlds before comparing the two simulators.
The legacy `simple` debug layout retains its original behavior.
`test/test_track_layout.py` checks spacing, width, closure and crossings
across several seeds for both styles.

`acceleration` is a fixed rules layout instead (DD.4.2, D.9.1): a straight
along +x, a 7 m entry lane, start line at x = 0, 75 m to the finish, then a
75 m exit lane. Its 4.9 m is clear width between cone bases, so centers sit
half a base further out and `pairing_width_m('acceleration')` (5.13 m) is
the spacing the Gazebo launch gives the track builder. The kinematic launch
does not pass it yet, so acceleration runs only under Gazebo. Large
orange cones flank both lines and small orange marks the exit lane; they
stay in the left/right lists, with the kind on each marker. Only
`cone_spacing_m` applies, not the seed or `width_m`.
`test/test_acceleration.py` checks the layout.

Node cleanup tolerates an already-stopped ROS context after launch shutdown.

## Rules-compliant autocross

`track_style:=rules_autocross` selects the DD.4.4.1 geometry envelope from
[the track rules reference](../../../docs/rules/fsae-track-and-events.md).
The existing `autocross` generator and its MVP seeds remain unchanged.

The new generator joins straight pieces, smooth alternating-offset chicanes
and slaloms, a half-turn whose radius decreases toward its apex, and a
constant-radius return turn. The two lanes run back beside each other, centered about the map origin.
A private seeded random source selects lengths, feature amplitudes, and
turn radii; the same seed and dimensions reproduce the same ordered pairs.
This is a bounded course family, not a sampler of every possible legal layout.

| Check | Acceptance |
| --- | --- |
| Centerline lap length, including closure | 200–500 m |
| Longest straight, including one across the index seam | At most 80 m |
| Tightest centerline circumradius | At least 4.5 m (9 m turning diameter) |
| Clear width between boundary segments | At least 3 m |
| Boundary intersections | None, within a side or across sides |
| Remote same-side sections | At least 0.5 m between conservative cone-base envelopes |

The last separation is a simulation guard, not a quoted event requirement.
`check_envelope(left, right)` returns the four measured limits and rejects
invalid geometry. It checks segment distances, not just distances between
cone vertices, so nearby return lanes cannot hide a crossing or a narrow gap.
Straightness uses a 1e-5 sine-of-heading-change tolerance. Turning diameter
is interpreted conservatively as a centerline diameter. These are layout
checks, not proof of vehicle drivability, staging, or event compliance.

For this style, `width_m` means **minimum clear width**, unlike legacy styles'
cone-center spacing. Paired centers include the square base's circumscribed
diameter and a 0.10 m chord/serialization reserve. Thus actual clear width
can slightly exceed the request. The radius is chosen with a reserve above
4.5 m; `radius_m`, `jitter_m` and `num_waypoints` apply only to legacy styles.
`cone_spacing_m` remains an upper bound on either boundary, capped at 2 m.
Widths below 3 m and nonpositive/nonfinite spacing fail immediately.

Each sampled candidate is checked after cone placement. Rejected candidates
consume the next values from the same seeded stream; after 100 failures the
generator raises an error instead of returning an invalid course. Tests
cover 100 seeds at four widths, known valid/invalid layouts, regeneration,
world serialization, and micrometre-rounded hashes of the five original MVP seeds.

Generate a world and run it from `autonomy/ros2`:

```bash
python3 src/lhr_gazebo/scripts/generate_world.py --style rules_autocross \
  --seed 1 --width 3 --output data/rules_autocross_seed1.sdf
ros2 launch lhr_gazebo gazebo_demo.launch.py track_style:=rules_autocross \
  seed:=1 width_m:=3 world:="$(pwd)/data/rules_autocross_seed1.sdf" \
  gui:=false rviz:=false perception:=lidar estimator:=ekf
```

Use the same width and spacing for world generation and launch. Gazebo's
planner pairing width accounts for cone bases in both perception branches.
Metrics reads the selected world's ordered pairs, including return sections;
no reference route is supplied to perception or control. See the
[gate runner](../../../testing/tools/gate/README.md) and
[fault runner](../../../testing/tools/fault_injection/README.md) for campaigns.
