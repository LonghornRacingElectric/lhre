---
name: edit-vehicle
description: Change a parameter of the current car in vehicle/vehicle.yml, update vehicle/bundle.yml, and check that BobSim still loads it. Use when a measurement, a design change or a study result changes a vehicle number, or to check a changed copy as a candidate.
---

# Edit the vehicle

## Before you start

1. Get the source of the new value: a measurement, a CAD export, a
   datasheet or a study. Write it down. The PR needs it.
2. Find the key in `vehicle/vehicle.yml`. The unit is in the key suffix,
   for example `_m`, `_kg` or `_n_per_m`. Use SI units.
3. Read [The bundle](../../../../vehicle/README.md#the-bundle).
   `vehicle/bundle.yml` records which car the file is and how much of that
   is known.

## Procedure

1. Change the value in `vehicle/vehicle.yml`. Change nothing else.
2. Update `vehicle/bundle.yml`:
   - Set `members.vehicle.sha256` to the new hash. From the repo root, run
     `sha256sum vehicle/vehicle.yml`.
   - Set a new `bundle_id`. A changed member always needs one.
   - Update each entry that the change affects. Give the evidence status
     and the source. For example, a new front hardpoint set changes
     `axles.front.revision`, `axles.front.source` and
     `axles.front.transform`.
   - Keep `status: candidate`. Do not set `released`.
3. From `simulation/`, run:

   ```bash
   make test
   make check-vehicle BASE=origin/main
   ```

   `make test` must print `OK`. `make check-vehicle` must end with
   `candidate: software checks passed; not hardware approval`. In a git
   worktree, leave out `BASE=`. CI runs that part.
4. If the change affects the Modelica tier, run `make records`, then
   `make bobsim T=standard-build`. The build must pass.
5. If you changed wheelbase, track, mass or CG, find the same number in
   `autonomy/ros2/src/lhr_vehicle/config/vehicle.yaml`. That file is a
   separate copy. Tell the user. Do not change it unless they ask. The
   autonomy team owns it.
6. In the PR, give the old value, the new value, the unit and the source.
   Give the old and the new `bundle_id`.

## Check a candidate

A candidate is a changed copy of the car. It lives under `simulation/out/`.
Do not commit it. From `simulation/`:

```bash
mkdir -p out/my-candidate
cp ../vehicle/vehicle.yml ../vehicle/bundle.yml out/my-candidate/
# Edit out/my-candidate/vehicle.yml.
sha256sum out/my-candidate/vehicle.yml
```

In `out/my-candidate/bundle.yml`, set a new `bundle_id`, keep
`status: candidate`, set `members.vehicle.path` to
`simulation/out/my-candidate/vehicle.yml` and set `members.vehicle.sha256`.
Update the entries that changed. Then run:

```bash
make check-vehicle VEHICLE=out/my-candidate/vehicle.yml BUNDLE=out/my-candidate/bundle.yml
```

`VEHICLE` is relative to `simulation/`. Member paths in the bundle are
relative to the repo root. The check fails if the candidate keeps the
`bundle_id` of `vehicle/bundle.yml`. A candidate copy can never be
`released`.

## Evidence status

Use the weakest status that is true.

- `measured`, `cad_export` or `declared` only with a named source that
  states the value.
- `assumed` for an inference from a named source, for example the driven
  axle from the BobSim driveline record.
- `guessed` for a value copied from an older revision, estimated, or typed
  by hand from a screen or a drawing. A `hand-entered` source must be
  `guessed`.
- `unavailable` with `value: null` when no source gives it.

## Do not

- Do not change `paths:`. Its entries resolve from `simulation/bobsim`.
  Only a BobSim pin move changes them, when BobSim moves a folder. See
  [bump-bobsim](../bump-bobsim/SKILL.md).
- Do not change `modelica_initialization`. See
  [bobsim.md](../../bobsim.md#modelica-initialization).
- Do not add a second vehicle file or a `vehicle/candidates/` folder.
  Studies and candidates write a copy to `out/`.
- Do not set `status: released`. Only a person with a release decision to
  cite does that.
- Do not fill an unknown entry with a plausible value. Write
  `unavailable`. Do not invent a revision, a CAD or SVN reference, a datum
  or an approval.
- Do not change line endings. The files are LF. The hashes depend on it.
