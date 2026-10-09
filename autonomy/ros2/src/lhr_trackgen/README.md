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
fixtures. This marks a scene gate, not an implemented lap timing system or
an assertion that the generated track is event compliant. Entry/exit layouts
remain future work; all four cone asset types are available in Gazebo.

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
