# Track Generator

`publish_cones` publishes synthetic ground-truth cone markers on
`/lhr/track/cones`. The default is the existing seeded autocross generator;
`simple` and `oval` also produce closed tracks.

## Acceleration

After building and sourcing the ROS workspace:

```bash
ros2 run lhr_trackgen publish_cones --ros-args -p track_style:=acceleration
```

The ROS-independent `generate_acceleration_track()` function in
`lhr_trackgen.acceleration` returns a dictionary of cone-center `(x, y)` lists:

| Namespace | Layout |
| --- | --- |
| `left_cones` | 14 small blue cones along the left boundary |
| `right_cones` | 14 small yellow cones along the right boundary |
| `orange_small_cones` | Entry lane and exit/stopping lane, both sides |
| `orange_large_cones` | Both sides, before and after the start and finish lines |

Travel is in the +x direction. Start is at x=0 m, finish at x=75 m, and
the exit lane ends at x=150 m. There is **4.9 m of clear width between
cone bases**, so the small-cone centers are at y=+/-2.564 m and large-cone
centers at y=+/-2.5925 m. Boundary spacing is 75/13 m (~5.77 m).
Small-cone dimensions are 228 x 228 x 325 mm; large cones are
285 x 285 x 505 mm. RViz uses sphere markers with these bounding dimensions,
centered at half-height; these are visual markers, not physical cone meshes.

The 75 m course, minimum clear width, approximate 6 m boundary spacing,
cone colors/sizes, and maximum 75 m stopping distance follow the
[2026 base rules, D.9.1](https://www.formulaknightsracing.com/_files/ugd/742c10_60e00afcfde64f9cae59db562c356c9c.pdf#page=135)
and [the Notion driverless supplement, DD.1/DD.4.2](https://www.notion.so/314e26707bba80d2ae3fd338096c35c9).
This is one simulation arrangement, not an official surveyed map: the
12 m entry lane, 1 m timing-marker offsets, exact cone counts, aligned rows,
and straight exit lane are choices. The exit endpoint is a stopping limit,
not a recommended stop target. Vehicle staging must place the nose 0.30 m
behind the start; this function does not set a vehicle pose.

Acceleration is deterministic and ignores the loop-specific `seed`,
`num_waypoints`, `radius_m`, `jitter_m`, `width_m`, and `cone_spacing_m`
publisher parameters. Its rule dimensions are fixed in the function.

The Gazebo world generator exports this same four-color layout with
`python3 src/lhr_gazebo/scripts/generate_world.py --style acceleration` from
`autonomy/ros2/`. It writes `worlds/acceleration.sdf` and stages the model's
collision-body nose 0.30 m before the start, using `lhr_vehicle` geometry.
Launch `track_style:=acceleration` to select the acceleration planner and
open-path pure pursuit. See the [Gazebo reference](../lhr_gazebo/README.md).

## Verification

Geometry tests run without ROS:

```bash
PYTHONPATH=src/lhr_trackgen python3 -m pytest src/lhr_trackgen/test/test_acceleration.py
```

Run the full package tests in the ROS environment with
`colcon test --packages-select lhr_trackgen` from `autonomy/ros2/`.
The publication test requires ROS and is skipped when ROS is unavailable.
