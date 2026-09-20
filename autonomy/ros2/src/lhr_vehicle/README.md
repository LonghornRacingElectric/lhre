# lhr_vehicle

One file, `config/vehicle.yaml`, holds Orion's physical parameters —
wheelbase, track, wheel radius, masses, steering limits, the LiDAR mount —
and every consumer reads it from there:

- `lhr_control` (pure pursuit) and `lhr_sim_kinematic` take their
  `wheelbase` / `max_steer` parameter *defaults* from it. Launch-file
  overrides still work; the file only replaces the hardcoded fallbacks.
- `lhr_gazebo/joint_cmd_adapter` uses it for the Ackermann and
  differential-speed math.
- `lhr_perception/lidar_cone_detector` uses the LiDAR mount position for
  the sensor→`base_link` transform and the body dimensions for the
  self-exclusion box.
- The Gazebo model `lhr_gazebo/models/fsae_vehicle/model.sdf` is
  **generated** from it (see below); wheel poses, joint limits, masses and
  sensor poses all derive from the same numbers. The steering joints get
  the Ackermann *inner* angle at full lock, since that is what they see.

Why: before this package the wheelbase and the steering limit were repeated
in four places with four different steering values (0.45, 0.55, 0.69 and
0.7 rad), and none of the numbers had a stated origin.

## Frame

`base_link` is the rear-axle center at ground level, x forward, y left,
z up. Sensor positions are given in that frame.

## Provenance

Each value carries a tag in the YAML: `BobSim` for numbers derived from
VMOD's [BobDyn/BobSim](https://github.com/BobDyn/BobSim) `vehicle.yml` for
Orion, `ASSUMED` for simulation placeholders that still need measuring on
the car. When you measure one, replace the number and drop the tag.

## Usage

```python
from lhr_vehicle import load_vehicle

veh = load_vehicle()
veh.wheelbase_m, veh.track_m, veh.max_steer_rad, veh.lidar_position_m
```

`load_vehicle()` finds the installed copy through the ament index, or the
source-tree copy when imported straight from the checkout. The model
generator always passes the source-tree path explicitly so an installed
copy can never be stale relative to the YAML being edited.

## Regenerating the Gazebo model

After editing `vehicle.yaml`:

```bash
cd ros2
python3 src/lhr_gazebo/scripts/generate_vehicle_model.py
./scripts/build.sh
```

Commit the regenerated `model.sdf` alongside the YAML change — it is
checked in, like the generated world files. `generate_vehicle_model.py
--check` reports whether the two agree, and `lhr_gazebo`'s tests fail when
they don't, so CI catches a forgotten regenerate.
