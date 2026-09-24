---
name: edit-vehicle
description: Change a parameter of the current car in vehicle/vehicle.yml and check that BobSim still loads it. Use when a measurement, a design change or a study result changes a vehicle number.
---

# Edit the vehicle

## Before you start

1. Get the source of the new value: a measurement, a CAD export, a
   datasheet or a study. Write it down. The PR needs it.
2. Find the key in `vehicle/vehicle.yml`. The unit is in the key suffix,
   for example `_m`, `_kg` or `_n_per_m`. Use SI units.

## Procedure

1. Change the value in `vehicle/vehicle.yml`. Change nothing else.
2. From `simulation/`, run:

   ```bash
   make test
   ```

   It must print `OK`.
3. If the change affects the Modelica tier, run `make records`, then
   `make bobsim T=standard-build`. The build must pass.
4. If you changed wheelbase, track, mass or CG, find the same number in
   `autonomy/ros2/src/lhr_vehicle/config/vehicle.yaml`. That file is a
   separate copy. Tell the user. Do not change it unless they ask. The
   autonomy team owns it.
5. In the PR, give the old value, the new value, the unit and the source.

## Do not

- Do not change `paths:`. Its entries resolve from `simulation/bobsim`.
- Do not add a second vehicle file. Studies that need a changed vehicle
  write a copy to `out/`.
- Do not change line endings. The file is LF.
