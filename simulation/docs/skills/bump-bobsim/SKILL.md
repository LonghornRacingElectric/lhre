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
   `make bump-bobsim` stops when this command prints anything.

## Procedure

1. From `simulation/`, move the pin:

   ```bash
   make bump-bobsim REF=<ref>
   ```

   The target removes the nested submodules first
   (`git submodule deinit -f --all`). Then it checks out `<ref>` and gets
   the submodules of that commit. Without the first step, a nested
   submodule that BobSim moved stays behind as an untracked folder. BobSim
   `6629ab8` (#82) moved BobLib to `engines/boblib` in this way.
2. Read what changed:

   ```bash
   git -C bobsim log --oneline <old-sha>..HEAD
   ```

   Look for renamed modules, changed `vehicle.yml` keys and changed
   targets.
3. Put each commit in one class: physics, numerics, model input, identity,
   layout, build, docs, reports or app. List the physics, numerics and
   model-input commits in the PR and in
   [bobsim.md](../../bobsim.md#the-pin). A layout move must not hide them.
4. If BobSim moved a module or a folder, change lhre to match: imports in
   `tools/` and `studies/`, `BOBLIB` and the generator module in the
   `Makefile`, `paths:` in `vehicle/vehicle.yml`, and the docs. Add each
   move to the old to new table in `docs/bobsim.md`. Do not change physics,
   vehicle values or study logic in the same PR.
5. Rebuild and test:

   ```bash
   make build
   make test
   ```

   `make test` fails when lhre uses an old BobSim name or an import that
   does not resolve.
6. Check that `make records` writes into BobSim's BobLib, then undo it:

   ```bash
   make records
   git -C bobsim/engines/boblib status --short
   make records-undo
   git -C bobsim status --short
   ```

   The first status lists only the record files. The second prints
   nothing. If `make records` stops on the initialization, read
   [bobsim.md](../../bobsim.md#modelica-initialization).
7. For each study that uses a BobSim module that changed, fix its imports
   in the same PR. Then run it again with `make study S=<name>`. If you do
   not run it again, add a status line to its README: the result is
   historical at the old SHA and stale at the new one.
8. If `simulation/bobsim/vehicle.yml` gained new keys, add them to
   `vehicle/vehicle.yml` with values for our car. Each value needs a named
   source. If there is no source, ask.
9. Commit `simulation/bobsim` and your fixes together. In the PR, list the
   BobSim commits and what they change for us.

## Checks before you finish

- `make test` passes.
- `git -C simulation/bobsim status --short` prints nothing.
- Every study you changed imports at the new pin. Its README says if its
  result is stale.
- `docs/bobsim.md` is still correct, including the times table and the old
  to new table.
