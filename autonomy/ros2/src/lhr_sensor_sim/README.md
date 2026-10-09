# lhr_sensor_sim

Takes the ground-truth cones on `/lhr/track/cones`, keeps the ones inside
the vehicle's field of view and range, and republishes them on
`/lhr/sensor/cones_detected`. It is a visibility filter, not a sensor model.

Why it exists: it lets planning and control run without a point cloud or a
renderer, so the kinematic stack works headless, on any OS, and in CI.

What it costs: it hands out cone positions directly, so
`lhr_perception/lidar_cone_detector` is never exercised by the kinematic
stack. That is the reason perception currently has no automated test at
all. This package is a shortcut with a name, kept so it can be retired
once a synthetic LiDAR publishes `/lhr/lidar/points` without Gazebo.

## Parameters

| Parameter | Default | What it does |
| --- | --- | --- |
| `fov_deg` | 200.0 | Angular width of the detection wedge |
| `max_range_m` | 20.0 | Cones beyond this are not detected |
| `min_range_m` | 0.5 | Cones closer than this are not detected |
| `detection_hz` | 10.0 | Detection tick rate |
| `noise_std_m` | 0.0 | Gaussian position noise, metres std dev |
| `false_negative_rate` | 0.0 | Chance a visible cone is missed on a tick |
| `seed` | 1 | Seeds this node's random stream |

## Accumulation

A cone is measured once. After it enters `_accumulated` it is never
re-measured and never forgotten, so position noise is applied a single
time and then persists for the whole run. That matches a mapping sensor
more than a tracking one, and it means the detected set only ever grows.

## Determinism

`seed` seeds a node-local `random.Random` rather than the process-global
`random`. The global one is shared with every other node in the process,
so draws interleave and no run repeats.

What that does guarantee: a given seed produces the same noise and the
same missed cones for a given sequence of draws.

What it does not: the draw *order* depends on how many cones enter the
field of view on each tick, which depends on when odom arrives relative
to the detection timer. The kinematic stack is stable enough to repeat.
Gazebo is not, and never will be. If run-to-run equality turns out to
need more than this, derive each cone's noise from its identity
(`seed`, `ns`, `id`) instead of from a sequential stream, which is
deterministic whatever the order.

One surprise worth knowing: the false-negative draw happens per tick, not
per cone, so at 10 Hz a cone rejected once is usually detected within a
second. The parameter models a transient miss, not a cone the stack never
sees.

Cone triangle vertices and stripe colors are preserved in detections and
visualization. The simplified sensor copies each scene marker before adding
position noise, so it cannot alter the ground-truth scene positions.
