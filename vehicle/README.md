# vehicle

The parameters of the current car. This is the one source of truth for
simulation. Other packages can use it too.

| File | What it holds |
| ---- | ------------- |
| `vehicle.yml` | Mass, CG, inertia, suspension, tires, aero and powertrain. Schema `boblib.vehicle.v1`. |
| `tires/*.tir` | Tire models that `vehicle.yml` names. |

## Use it

- **Simulation:** `simulation/` reads this file through the `BOBSIM_VEHICLE`
  environment variable. See [simulation/README.md](../simulation/README.md).
- **Bazel:** depend on `//vehicle:vehicle.yml` or `//vehicle:tires`. Both are
  public.

## Change it

1. Edit `vehicle.yml`. Keep SI units and the unit suffix in each key
   (`_m`, `_kg`, `_n_per_m`).
2. Run `make -C simulation test`. It fails if BobSim cannot load the file.
3. Say in the PR what changed and why. A study result is the best reason.

## Gotchas

- Entries under `paths:` resolve from the BobSim root (`simulation/bobsim`),
  not from this directory. That is why the tire path is
  `../../vehicle/tires`.
- Autonomy has its own copy of some of these numbers in
  `autonomy/ros2/src/lhr_vehicle/config/vehicle.yaml`. It does not read this
  file yet. If you change wheelbase, track or mass, tell the autonomy team.
- Files here are LF on every OS (`.gitattributes`). This keeps the
  `vehicle_sha256` in study provenance the same on Windows and Linux.
