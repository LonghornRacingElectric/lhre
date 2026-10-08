# lhr_vehicle

One file, `config/vehicle.yaml`, holds Orion's physical parameters
(wheelbase, track, wheel radius, masses, steering limits, the LiDAR mount)
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

## Fingerprinting the file

`vehicle_sha256()` hashes the file's bytes. Anything derived from these
numbers stamps it, so a stored result says which car description produced
it. A commit id does not cover that, because the file is often edited
without being committed.

```python
from lhr_vehicle import vehicle_sha256

vehicle_sha256()   # 'd364f17c...', 64 hex characters
```

Same formula as `simulation/tools/provenance.py`, so a sim run and a
BobDyn study are comparable on that one field. `lhr_metrics` writes it on
every row.

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

Commit the regenerated `model.sdf` alongside the YAML change: it is
checked in, like the generated world files. `generate_vehicle_model.py
--check` reports whether the two agree, and `lhr_gazebo`'s tests fail when
they don't, so CI catches a forgotten regenerate.

## Seeing the car

`vehicle_viz` publishes the car on `/lhr/vehicle/body`, in `base_link`. It
exists because a 3D viewer otherwise shows a grid, a line and a cloud,
with nothing to say where the car is or which way it points.

```bash
ros2 run lhr_vehicle vehicle_viz
```

By default it draws Orion's actual CAD, as a `MESH_RESOURCE` marker
pointing at [`meshes/orion.stl`](meshes/README.md). The mesh is in metres
and its origin is the front axle, so the marker's whole transform is a
translation of one wheelbase along x, read from this file rather than
hardcoded.

In Foxglove, set **3D panel → Scene → Mesh up-axis → Z-up** (already
set in the committed `lhr_sim` layout). STL has no up-axis metadata;
loading this Z-up CAD as Y-up rolls the car onto its side. Existing
layouts need that setting changed or the updated layout imported.

```bash
ros2 run lhr_vehicle vehicle_viz --ros-args -p use_mesh:=false
```

`use_mesh:=false` falls back to the older primitives, a chassis box and
four cylinders. That fallback is kept deliberately: a mesh marker names an
asset the viewer has to **fetch**, which works against a live
`foxglove_bridge` but not when replaying a bag, because a bag has no asset
server. If a recorded run has to open as a visible car, record it with
`use_mesh:=false`.

Serving the mesh needs no bridge configuration. `foxglove_bridge`'s
`asset_uri_allowlist` already defaults to a pattern that allows
`package://<pkg>/<path>/<name>.stl`.

The demo launch starts the node, and `mvs_demo.launch.py` records the
topic. The markers are latched and published once rather than on a timer:
the car's shape does not change, and a marker array per tick would bloat
every bag for no information.

The primitives are still worth understanding, because `use_mesh:=false`
draws them and their two halves differ in provenance. The wheels and axles
are real: wheelbase, track and wheel radius all come from BobDyn (see
[Provenance](#provenance)). The chassis box is not, since
`body_length_m`, `body_width_m` and `body_height_m` are `ASSUMED`
placeholders. That gap is exactly what the mesh closes, and
[`meshes/README.md`](meshes/README.md) records where it came from and the
one body in it that is reconstructed rather than extracted.
