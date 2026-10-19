---
name: bobsim-boundary
description: Keep the lhre simulation stack (MIT) apart from BobSim (GPL-3.0, a submodule), and work across the line between them. Use when you write or review study or tool code that uses BobSim, when BobSim cannot represent a variable, when you want to patch, copy or monkeypatch BobSim, when a BobSim result looks wrong, or before a BobSim pin bump.
---

# Work across the BobSim boundary

BobSim is the simulation runtime. It is a git submodule at
`simulation/bobsim` under GPL-3.0. The lhre simulation stack is MIT:
`simulation/studies/`, `simulation/tools/` and `vehicle/`. lhre code uses
BobSim only through imports and `make` targets.

## Rules

1. **Do not edit `simulation/bobsim/`.** `make records` writes Modelica
   records into the submodule. `make records-undo` removes them. Before
   you commit, this command must print nothing:
   `git -C simulation/bobsim status --short`.
2. **Import BobSim. Do not copy it.** BobSim is GPL-3.0 and lhre is MIT.
   Every use of BobSim in lhre is an `import`. Do not paste BobSim code,
   not even one function.
3. **Use public names.** In BobSim, a module, class or function name that
   starts with `_` is private. Package names such as `_0_Utils` and
   `_5_App` also start with `_`. They are not private.
    - Import a private name only when no public name does the job. List
      each private import in the study README. Expect it to break on a pin
      bump.
    - Example: the front-ackermann study imports `_mf52_fx_pure` and
      `_mf52_fy_pure` from `_5_App.tire_eval`. At `4da577a`, BobSim has no
      public function that gives MF5.2 force at one load and one slip.
    - Example: make a `GGVMap` with the public `GGVMap.from_arrays`. Do not
      make one from the private `_GGVSlice`.
4. **Check that the tool can see the effect.** Before you use a BobSim
   tool for a variable, run a case where the effect must show. A tool
   with no input for the variable gives a null result that means nothing.
    - Example: in `_0_Utils/dyn_py/models.py`, `ModelInputs.steering_rad`
      is one angle for both front wheels. The reduced-order models, the
      transient lap and the GGV and YMD envelopes use it. None of them can
      see Ackermann.
    - The Modelica tier runs fixed maneuvers only: steady state, ramp
      steer, step and sine steer, and four-post. It does not run a lap.
5. **Fill a gap on the lhre side.** In the study, compute what BobSim
   cannot. Then pass the result to a public BobSim tool. Example: compute a
   lateral-g envelope in the study, make a map with `GGVMap.from_arrays`,
   and solve the lap with `solve_qss_lap`. Put code that more than one
   study needs in `simulation/tools/`.
6. **Fix BobSim upstream.** If BobSim has a defect, open an issue or a PR
   in [BobDyn/BobSim](https://github.com/BobDyn/BobSim). When the fix is
   merged, move the pin with [bump-bobsim](../bump-bobsim/SKILL.md). Do
   not monkeypatch BobSim at run time. Until the pin has the fix, name the
   defect and the workaround in the study README.
7. **Keep one vehicle file.** The vehicle is `vehicle/vehicle.yml`. A
   study reads it from `BOBSIM_VEHICLE`. To test a change, change the
   value in memory or in a copy under `OUT_DIR`. `make bobsim T=...`
   targets read `simulation/bobsim/vehicle.yml` until BobSim reads
   `BOBSIM_VEHICLE`. See [bobsim.md](../../bobsim.md#our-vehicle-and-bobsim).
8. **Run BobSim through `make`.** Run each `make` target from
   `simulation/`. Every target runs in Docker. Do not run BobSim with a
   host Python.
9. **Make silent failures stop the run.** Some BobSim paths drop an input
   and continue. Check that a changed input changed the output.
    - At `4da577a`, `_3_StandardSim/_modelica_runner.py` drops a run-time
      override whose name is not in the build's `_init.xml`. It logs
      nothing.
    - The compiled Modelica model ignores an override of a calculated
      parameter. It writes only a warning to `run.log`: "It is not possible
      to override". Stop the run when `run.log` has that text.
10. **Record what you used.** `make study` writes the BobSim SHA to
    `provenance.json`. Show it in the README Provenance section. List the
    private names from rule 3 next to it. If the pin changes, run the
    study again before you use its result.

## Method

1. Name what you need from BobSim: a function, a model or a target.
2. Find a public name for it in `simulation/bobsim/` at the pinned commit.
   Read the code. Do not trust a name from an older commit.
3. Run a case where the effect must show (rule 4). If BobSim cannot see
   it, write the missing part in the study.
4. If BobSim gives a wrong result, write the smallest case that shows it.
   Open the upstream issue or PR with that case (rule 6). Put the
   workaround in the study only.
5. Run the study with `make study S=<name>`. Change one input and check
   that the output changes (rule 9).
6. Write the README: the private imports, the defects and workarounds, and
   the provenance.
7. After `make bump-bobsim REF=<ref>`, check that each private name still
   exists. For example:

    ```bash
    git -C simulation/bobsim grep -nE "(def|class) _mf52_fx_pure\b" -- '*.py'
    ```

## Checks before you finish

Run these from the repo root.

- `git -C simulation/bobsim status --short` prints nothing. If
  `make records` ran, run `make records-undo` from `simulation/`.
- `git diff --stat origin/main...HEAD -- simulation/bobsim` prints
  nothing, unless the PR moves the pin on purpose.
- This command lists the private BobSim imports of a study. Each one is in
  the README. It does not see an import split over lines.

    ```bash
    grep -nE "^(from|import) " simulation/studies/<name>/run.py | grep -E "[ .,]_[A-Za-z]"
    ```

- The diff has no code copied from `simulation/bobsim/`.
- No code assigns to a name in a BobSim module, for example with
  `setattr` or `module.name = value`.
- The README Provenance section shows the BobSim SHA.
- A changed input changed the output.
- `make test` passes.
