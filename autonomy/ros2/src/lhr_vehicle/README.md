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
- The Gazebo models `lhr_gazebo/models/fsae_vehicle/model.sdf` and
  `model_stereo.sdf` are
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

Commit the regenerated `model.sdf` and `model_stereo.sdf` alongside the YAML
change. The default model omits cameras; the stereo variant adds the rig.
Both are checked in, like the generated world files.
`generate_vehicle_model.py --check` reports whether both agree with the YAML
and templates, and `lhr_gazebo`'s tests fail when they don't, so CI catches a
forgotten regenerate.

## Stereo camera

The car uses one ZED 2i with the 2.1 mm wide lens. `sensors.stereo_camera`
contains its 0.12 m baseline, rig-center pose in `base_link`, and simulated
image size/rate/FOV. `load_vehicle().stereo_camera` exposes these values and
pixel intrinsics to the model generator and camera transforms. Invalid or
missing camera configuration fails explicitly; rebuild installed packages
after updating the YAML.

The initial mount is assumed. Images use an ideal rectified pinhole with
zero distortion and square pixels, rather than a physical camera calibration.
At 1280x720, the 110-degree horizontal FOV implies about 77.6 degrees vertical;
manufacturer FOV values do not supply exact rectified intrinsics. Measure the
mount and obtain device calibration before matching the physical unit.
See the [stereo design](../../../docs/plans/zed2i-stereo.md) for scope.
