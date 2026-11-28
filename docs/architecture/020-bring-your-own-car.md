# ADR-020: lhre hands BobSim its car; nothing writes into the submodule

- **Status:** Accepted
- **Date:** 2026-11

## Context

[ADR-012](012-simulation-outside-bazel.md) pins BobSim as a submodule and
says simulation reads our car through `BOBSIM_VEHICLE`. BobSim's targets
did not read that variable. To run one on our car, `make car` wrote our
Modelica records into the submodule's BobLib (`make records`), copied our
file over `bobsim/vehicle.yml`, ran the target, and then put every file
back (`make records-undo`). That took a 230-line shell wrapper, a list of
record files that each BobSim bump could change, a check that no
executable was older than the records, and recovery steps for a run that
stopped half way. Results landed inside `bobsim/` too.

BobSim belongs to the BobDyn organization, not to us. Nothing
specific to our car should go upstream, and lhre should not need a fork.

## Decision

- BobSim takes the car and the output folder from outside: `BOBSIM_VEHICLE`
  names the car file, `BOBSIM_OUT` the folder for every generated file, and
  both Modelica builds compile a staged copy of BobLib that holds that
  car's records ([BobSim #176](https://github.com/BobDyn/BobSim/pull/176)).
  The feature is generic: any team can run its own car this way.
- `make car` runs `simulation/tools/car.py` in the container. It sets both
  variables for one target, keeps the Modelica builds between runs, copies
  the outputs out, and writes `identity.json` (schema `lhre.car-run.v2`).
- `make records` and `make records-undo` are gone. Nothing lhre runs writes
  into `simulation/bobsim/`. `make bobsim` empties `BOBSIM_VEHICLE` so it
  keeps running BobSim's own car.
- The submodule stays. A fork or a package would add a second thing to
  keep in step, and the pin is already the one place that says which
  BobSim gave a result.

## Alternatives considered

- **Keep writing into the submodule.** It works, but every run dirties
  `bobsim/` and depends on the cleanup running. A power cut left a broken
  checkout that `make bump-bobsim` and `make unit` then refused.
- **Fork BobSim and keep our car in the fork.** It puts our car next to
  BobSim's code, which is what this decision avoids, and every upstream
  change then needs a merge.
- **Stage BobLib ourselves in lhre.** It copies BobSim's knowledge of which
  files the generator writes into lhre, which is the list that broke on
  bumps.

## Consequences

- A BobSim pin before BobSim #176 cannot run `make car`.
- `make car` output is the same layout as before, so CI artifacts and
  `make four-post-acceptance` read it unchanged.
- A first run on a new car compiles BobLib once into
  `out/car/<target>/.bobsim/`. `make clean` deletes that cache.
- [simulation/docs/bobsim.md](../../simulation/docs/bobsim.md#run-a-bobsim-target-on-our-car)
  has the steps.
