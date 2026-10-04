# lhr_lidar_sim

A synthetic Livox Mid-360: beam pattern, ray casting against cones and
ground, seeded noise and dropout. Publishes `sensor_msgs/PointCloud2` on
`/lhr/lidar/points`, which is the topic the perception lane consumes, so
this and a real sensor are interchangeable from the detector's side.

The model exists because **Gazebo cannot represent this sensor**. A
`gpu_lidar` is defined by `<horizontal>` and `<vertical>` sample counts,
a uniform grid, and the Mid-360's defining property is a non-repetitive
rosette that never samples the same direction twice. Modelling it in
numpy is the more faithful option here, not the cheaper one.

## Layout

| File | Role |
| --- | --- |
| `mid360.py` | Beam pattern and range model. No ROS. |
| `scene.py` | Ray casting against ground and cones. No ROS. |
| `sensor.py` | Mount pose and the per-frame pipeline. No ROS. |
| `lidar_sim_node.py` | The ROS wrapper. |

Only the last file imports ROS. The other three run and are tested
anywhere numpy does, including a macOS laptop with no ROS install, which
is where this work actually happens.

## What is real and what is a model

Taken from the datasheet and held exactly:

- 360 degree horizontal field of view.
- The asymmetric vertical field of view, **-7 to +52 degrees**. The
  sensor looks mostly *up*. That single fact is why mount pitch matters.
- 200 kHz point rate, so 20,000 beams per frame at 10 Hz.
- Non-repetition: the pattern never closes on itself, so dwelling longer
  keeps adding coverage instead of re-measuring the same directions.

A model, and not the real device:

- The beam's path *inside* that field of view. Livox does not publish
  the Risley-prism geometry, so two incommensurate sweeps stand in for
  it, with their frequency ratio set to the golden ratio so the pattern
  cannot repeat. This reproduces the non-repetition and the scan-line
  structure a clustering algorithm sees. It is not their curve.

## The density assumption, and why it matters

`elevation_profile` selects how beams distribute in elevation.
`rosette` uses a sine sweep, which lingers at its turning points and so
bunches beams toward the edges of the field of view, as an oscillating
scanner does. `uniform` uses a triangle sweep and spreads them evenly.

This is not a cosmetic switch. **Returns on one cone, accumulated over
20 frames:**

| Range | rosette, 0 deg | rosette, 20 deg | uniform, 0 deg | uniform, 20 deg |
| --- | --- | --- | --- | --- |
| 5 m | 196 | 70 | 100 | 103 |
| 10 m | 30 | 17 | 26 | 27 |
| 20 m | 14 | 8 | 13 | 12 |

Under `rosette` a level mount looks dramatically better. Under
`uniform` the mount pitch barely matters at all. So that apparent
finding **is an artifact of the density guess, not a property of the
Mid-360**, and this model cannot currently recommend a mount pitch. A
defensible answer needs the real angular density, which means Livox's
pattern specification or measurements from a real unit.

Run any study both ways. A conclusion that survives both does not rest
on the guess; one that does not survive is not a conclusion yet.

## What the model does say

These hold under both profiles, to within about a factor of two, and are
the numbers worth planning against:

- **A cone at 10 m returns roughly 2 to 3 points per frame.** At 20 m it
  is closer to one. Detection range is bounded by this, and speed by
  detection range.
- **Cone returns are about 1% of the cloud.** On a 24-cone field: 4,152
  returns per frame, of which 41 land on cones and the rest on the
  ground. A detector has to find a handful of points among thousands.
- **Ground reach is short.** Only the bottom 7 degrees of the field of
  view can see the ground at all, so a level mount at 0.55 m reaches
  about 4.5 m of ground before its beams pass over the horizon.

The first of those was cross-checked against a hand calculation: a cone
at 10 m subtends 1.31 by 1.86 degrees, which over 20 frames predicts
about 35 returns against 30 measured. The caster is not inventing or
losing points.

## Running it

As a node, inside the demo stack:

```bash
ros2 launch lhr_demo mvs_demo.launch.py lidar:=true mount_pitch_rad:=0.25
ros2 launch lhr_demo mvs_demo.launch.py lidar:=true record:=true
```

Off by default, deliberately: the gate's numbers were measured against
the cheat-mode `lhr_sensor_sim`, and swapping the perception front end
without saying so would make those numbers lie.

Standalone, with no ROS at all:

```python
from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose, points_on_cone
import numpy as np

sensor = Mid360Sensor(config=Mid360Config(),
                      mount=MountPose(pitch_rad=0.25), seed=1)
points, on_cone = sensor.frame_labeled(
    t=0.0, vehicle_x=0.0, vehicle_y=0.0, vehicle_yaw=0.0,
    cones_xy=np.array([[8.0, 0.0]]))
print(points.shape, on_cone.sum())
print(points_on_cone(sensor, 0.0, distance_m=10.0, frames=10))
```

One frame against a 24-cone field takes about 5 ms, against a 100 ms
budget at 10 Hz.

## Parameters

| Parameter | Default | Notes |
| --- | --- | --- |
| `mount_pitch_rad` | `0.0` | Positive is nose down. Unsettled, see above |
| `mount_roll_rad` | `0.0` | |
| `mount_yaw_rad` | `0.0` | |
| `seed` | `1` | Node-local stream, so a seed repeats |
| `frame_rate_hz` | `10.0` | |
| `point_rate_hz` | `200000.0` | Datasheet |
| `max_range_m` | `40.0` | Datasheet, at 10% reflectivity |
| `range_noise_std_m` | `0.02` | Applied along the beam, not per axis |
| `dropout_rate` | `0.0` | Returns lost outright |
| `elevation_profile` | `rosette` | Or `uniform`. See above |

The mount **position** comes from
[`lhr_vehicle`](../lhr_vehicle/README.md), because vehicle numbers live
in one file. The mount **orientation** does not, because it is the
answer the mount study owes and guessing it into `vehicle.yaml` would
make a placeholder look like a measurement.

## Gotchas

- `points_on_cone`'s `distance_m` is measured **from the sensor**, not
  from `base_link`. The mount sits 1.8 m ahead of the rear axle, and
  measuring from the axle shortens every range by that much, which near
  the field of view floor is the difference between seeing a cone and
  seeing nothing.
- Returns are labelled by the caster, not classified by position. At a
  cone's base a cone return and a ground return sit millimetres apart,
  so counting by position quietly counts ground as cone. `cast` returns
  an `on_cone` mask because it already knows.
- A ray aimed exactly at a cone's apex is the tangency case: the
  discriminant is algebraically zero and lands either side of it on
  rounding, measured at 1e-16 relative. `scene.py` clamps a small
  negative discriminant to zero, otherwise the nearest point of a cone
  is the one direction that can miss it, platform-dependently.
- **A crashed lidar node does not fail a run.** `ros2 launch` reports
  the metrics verdict only, so `lidar:=true` with a broken sensor still
  exits 0 with a clean lap. Check the log, or that
  `/lhr/lidar/points` is in the bag, before trusting a run that was
  supposed to use it.

## Not done yet

- Nothing consumes this in the kinematic stack. `lhr_perception`'s
  detector still only runs under `gazebo_demo.launch.py`; pointing it at
  this topic is what would give perception a headless test path.
- Cones are opaque cones of a single size, with no reflectivity model,
  so `dropout_rate` is a flat fraction rather than a function of range
  and colour. A dark cone at 30 m is the case that matters and this does
  not yet capture it.
- No motion within a frame. All 20,000 beams are cast from one pose, so
  at 15 m/s the 1.5 m travelled during a frame is ignored. That flatters
  the model at speed.
