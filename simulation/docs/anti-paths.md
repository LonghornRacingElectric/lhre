# Anti-dive and anti-squat records

`tools/anti_paths.py` gives one anti record for each axle and each
longitudinal force source: drive, hydraulic brake and regen. It reads the
car from `vehicle/vehicle.yml` and the torque topology from
`vehicle/bundle.yml`. It uses BobSim KinPy through public names only.

```python
from tools.anti_paths import anti_records
from tools.bundle import load_bundle
from common.vehicle_io import load_yaml

records = anti_records(load_yaml(vehicle_path), load_bundle(bundle_path).data)
```

## The force path decides the coefficient

The jacking coefficient `c = dFz/dFx` (N/N) is the vertical link force on
the body for each newton of contact-patch force, at a locked pose. Where
the wheel torque reacts sets the path.

| Reaction in the bundle | Example | `coeff_method` | What it computes |
| ---------------------- | ------- | -------------- | ---------------- |
| `upright` | Hub motor, outboard caliper | `contact_patch_path` | `-(dx_cp/ds)/(dz_cp/ds)`. The upright takes the whole tire wrench. |
| `chassis` | Inboard motor, differential, inboard caliper | `virtual_power_3d` | The chassis holds the wheel spin. The contact force does no work through the spin rate of the upright. |
| `null` (`unavailable`) | Not known | none | No number. Status `unavailable`. |

`s` is the contact-patch travel in KinPy (`CornerKinematics.solve_jounce`).
The tool takes five-point central differences with a 1 mm step at the
design pose (zero jounce, zero rack travel).

The side-view wheel-center formula `-(dx_wc/ds)/(dz_cp/ds)` is only a
cross-check. It is exact only at zero toe. `pose.cross_checks_n_per_n`
holds it and the BobSim DynPy instant-link value. DynPy uses the
contact-patch path for every force.

## Record

The keys follow contract C5.

| Key | Meaning |
| --- | ------- |
| `coeff_n_per_n` | The jacking coefficient in N/N. |
| `pct` | Per-axle-force anti. Front: `-100 c L / h`. Rear: `+100 c L / h`. |
| `axle_force_share` | The share of the source force on this axle. `pct` does not include it. The hydraulic brake share is `brake.front_bias`. |
| `h_m`, `h_basis` | CG height above the ground plane. `sprung_and_driver_cg_z` when `driver_mass` exists, else `sprung_cg_z`. |
| `wheelbase_m` | Front wheel-center x minus rear wheel-center x. |
| `pose` | Jounce, rack travel, step, tolerance in N/N, the h reference and the cross-checks. |
| `status` | `ok`, `partial`, `unavailable` or `invalid`. |
| `reason` | Why a value is missing, and each input that is only `assumed` or `guessed`. |

Sign convention: x forward, y left, z up. +Fx at the contact patch is
drive. Jacking +Fz lifts the body. Anti is positive when the jacking
opposes the pitch that the force causes. The sign of Fx does not change
`c` or `pct`.

## Status rules

- An unavailable reaction gives `unavailable`. The tool never picks a
  reaction location for you.
- A known reaction with an unavailable `frame.datum_z` gives `partial`.
  `coeff_n_per_n` does not depend on the datum. `pct` needs the CG height
  above the ground, so the tool withholds it.
- With a datum, `h_m` is the CG z minus the ground-plane z at the CG x,
  from the front and rear datum values.
- A KinPy solve failure or a nonfinite value gives `invalid`.
- Bad geometry raises `VehicleCheckError` with the key path. The tool runs
  `tools/vehicle_checks.py` first.

## The current car

`vehicle/bundle.yml` has no reaction location for drive, brake or regen,
and no datum. So every record is `unavailable`. To get a number, a named
source must state the reaction. Then set it in the bundle with its
evidence status.

## Tests

`tests/anti_oracle.py` is an analytical oracle. It does not import BobSim.
It has closed forms for an instant center and for parallel links, planar
and 3D statics with five two-force links, a virtual-power solve, and a
wrench split that closes force and moment balances. `test_anti_paths.py`
compares the tool with it. Agreement shows that the tool and the oracle
compute the same mechanism. It is not physical validation.
