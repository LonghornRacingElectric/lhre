---
name: bump-bobsim
description: Move the simulation/bobsim submodule to a newer BobSim commit and fix what the move breaks. Use when a study needs a BobSim change, or to take BobSim fixes.
---

# Bump BobSim

## Before you start

1. Get the BobSim commit, tag or branch. The change you need must be merged
   in [BobDyn/BobSim](https://github.com/BobDyn/BobSim).
2. Make sure `bobsim/` has no local changes:

   ```bash
   git -C simulation/bobsim status --short
   ```

   If `make records` changed files, run `make records-undo`.

## Procedure

1. From `simulation/`, move the pin:

   ```bash
   make bump-bobsim REF=<ref>
   ```

2. Read what changed:

   ```bash
   git -C bobsim log --oneline <old-sha>..HEAD
   ```

   Look for renamed modules, changed `vehicle.yml` keys and changed
   targets.
3. Rebuild and test:

   ```bash
   make build
   make test
   ```

4. Run each study that imports a module that changed. Fix the study in the
   same PR.
5. If `simulation/bobsim/vehicle.yml` gained new keys, add them to
   `vehicle/vehicle.yml` with values for our car.
6. Commit `simulation/bobsim` and your fixes together. In the PR, list the
   BobSim commits and what they change for us.

## Checks before you finish

- `make test` passes.
- Every study you changed runs with `make study S=<name>`.
- `docs/bobsim.md` is still correct, including the times table.
