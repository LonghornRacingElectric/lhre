# vehicle

The parameters of the current car. This is the one source of truth for
simulation. Other packages can use it too.

| File | What it holds |
| ---- | ------------- |
| `vehicle.yml` | Mass, CG, inertia, suspension, tires, aero and powertrain. Schema `boblib.vehicle.v1`. |
| `bundle.yml` | Which car `vehicle.yml` is, and how much of that is known. Schema `lhre.vehicle-bundle.v1`. See [The bundle](#the-bundle). |
| `tires/*.tir` | Tire models that `vehicle.yml` names. |

## Use it

- **Simulation:** `make test`, `make records` and studies read this file
  through the `BOBSIM_VEHICLE` environment variable. BobSim targets
  (`make bobsim T=...`) do not read that variable yet. For a Modelica
  target to use this file, run `make records` first. See
  [simulation/README.md](../simulation/README.md).
- **Bazel:** depend on `//vehicle:vehicle.yml`, `//vehicle:bundle.yml` or
  `//vehicle:tires`. All are public.

## The bundle

`bundle.yml` identifies the car that `vehicle.yml` describes. It records the
SHA-256 of each member file, the front and rear revisions, the source of the
coordinates, the frame, the datum, the mirror rule, CAD and SVN references,
the tire license, the setup and the torque topology.

- `bundle_id` names one set of member files. When the bytes of
  `vehicle.yml` or a tire change, the bundle needs the new `sha256` and a
  new `bundle_id`.
- `status` is `draft`, `candidate`, `released` or `superseded`.
- Each metadata entry is `{value, status, source}`. `source` says where the
  value comes from, or why it is unavailable.

| Evidence status | Meaning |
| --------------- | ------- |
| `measured` | Measured on the car or on a rig. |
| `cad_export` | Exported from CAD with no hand edit. |
| `declared` | A named document or owner states it. |
| `assumed` | Inferred from a named source. Nobody confirmed it. |
| `guessed` | Copied, estimated or typed by hand with no check. |
| `unavailable` | Not known. The value is `null`. |

Write an unknown value as `{value: null, status: unavailable, source: <why>}`.
Do not delete the key. A missing key fails the check. Do not write a
revision, a URL, a datum or an approval that no source states.

### What `make check-vehicle` checks

Run it from `simulation/`. It runs `python tools/bundle.py check` in the
container.

1. Each member hash matches the file bytes. The hash basis is the file as
   committed, with LF line ends. A source file outside git records the
   hash of its own bytes and `stored: external`. The check cannot read it.
2. The tires that `vehicle.yml` loads are the bundle tires, byte for byte.
3. BobSim loads `vehicle.yml`. BobSim at this pin has no vehicle validator.
   The check says so in its output. It runs the validator when a later pin
   has `common.vehicle_schema`.
4. A `draft` or `candidate` bundle can hold `assumed`, `guessed` and
   `unavailable` entries. The check lists them. A pass ends with
   `candidate: software checks passed. Not hardware approval.`
5. A `released` bundle needs `release.approved_by`, `release.decision_ref`
   and `release.date`. Every entry must be `measured`, `cad_export` or
   `declared`. Only `vehicle/vehicle.yml` can be released.
6. An axle source of kind `hand-entered` must be `guessed`. A hand
   transcription of hardpoints therefore cannot be released.
7. With `BASE=<git ref>`, the check fails when a member hash changed and the
   `bundle_id` did not. A released bundle can only change to `superseded`.
   In a git worktree, the container cannot read the `.git` link. Run a
   `BASE=` check in a normal clone. CI runs it on a pull request, against
   the base branch.

The tool never writes `bundle.yml`. Only a person sets `released`, and only
with a release decision to cite. A passed check is a software result. It is
not hardware approval.

### Tire data

A tire is restricted unless its license is `public` with status `measured`,
`cad_export` or `declared`. A missing license means restricted. Run
`python tools/bundle.py restricted` in the container to list the restricted
files. Do not upload them as CI artifacts. Record their hashes instead.

### The current bundle

`orion-baseline-1` is a `candidate`. `vehicle.yml` equals the BobSim
`4da577a` baseline ("Orion" in BobSim) except `paths` and
`modelica_initialization`. No record gives a suspension revision, CAD or
SVN reference, z datum, setup name, tire license, tire source or torque
reaction. The driven axle and the regen axle are `assumed` rear from the
BobSim driveline record. Run `make check-vehicle` to list every open entry.

## Change it

1. Edit `vehicle.yml`. Keep SI units and the unit suffix in each key
   (`_m`, `_kg`, `_n_per_m`).
2. Edit `bundle.yml`. Set `members.vehicle.sha256` to the new hash and set a
   new `bundle_id`. Update the entries that the change affects, with their
   source.
3. Run `make test` and `make check-vehicle` from `simulation/`. Both must
   pass.
4. Say in the PR what changed and why. A study result is the best reason.

To check a changed car without a commit, see
[edit-vehicle](../simulation/docs/skills/edit-vehicle/SKILL.md#check-a-candidate).

## Gotchas

- Entries under `paths:` resolve from the BobSim root (`simulation/bobsim`),
  not from this directory. That is why the tire path is
  `../../vehicle/tires`.
- `modelica_initialization: legacy` sets how the Modelica model starts
  after `make records`. See
  [simulation/docs/bobsim.md](../simulation/docs/bobsim.md#modelica-initialization).
- Autonomy has its own copy of some of these numbers in
  `autonomy/ros2/src/lhr_vehicle/config/vehicle.yaml`. It does not read this
  file yet. If you change wheelbase, track or mass, tell the autonomy team.
- Files here are LF on every OS (`.gitattributes`). This keeps the
  `vehicle_sha256` in study provenance and the bundle hashes the same on
  Windows and Linux.
