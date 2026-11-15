---
name: add-study
description: Answer one vehicle dynamics question about the current car with a new study in simulation/studies. Use when someone asks what a parameter change does, or asks to compare setups.
---

# Add a study

## Before you start

1. Write the question in one sentence. Get the user to confirm it.
2. Look in `simulation/studies/` for a study that answers it already. If one
   exists, run it again. Do not make a copy.
3. Pick the tier. Use the fastest tier that can answer the question. See
   [bobsim.md](../../bobsim.md#tiers).

## Procedure

1. Make the study from `simulation/`. Use a short kebab-case name that
   says the question:

   ```bash
   make new-study S=<name>
   ```

   It copies `tools/study_template/` to `studies/<name>/`: `README.md`,
   `study.yml` and `run.py`. See [studies.md](../../studies.md).
2. Write `study.yml`: the question, the decision, the bundle, the
   parameters and the sweep grid. Put every study choice here, not in
   `run.py`. See [studies.md](../../studies.md#studyyml).
3. Write `run.py`:
   - Read the inputs from `study.yml`.
   - Read the vehicle path from `os.environ["BOBSIM_VEHICLE"]`. Take car
     numbers from the vehicle and its bundle. Do not hard-code them.
   - Write every output to `os.environ["OUT_DIR"]`.
   - To change a parameter, load the vehicle, change the value, and write
     the changed copy to `OUT_DIR`. Do not edit `vehicle/vehicle.yml`.
   - Import BobSim. Do not copy its code. Do not change BobSim.
   - Write `summary.json` and `readme.json` to `OUT_DIR`.
   - If the study solves many independent cases, run them with
     `tools.parallel.map_cases`. See
     [studies.md](../../studies.md#parallel-cases).
4. Write `README.md` with a status line and four sections: Question,
   Method, Result, Provenance. See [studies.md](../../studies.md#readmemd).
   Keep it short and in simple technical English. Start the Result with the
   answer. A variant is a second config file, so it needs no code change.
   See [studies.md](../../studies.md#variants).
5. Run the study from `simulation/`:

   ```bash
   make study S=<name>
   ```

6. Read the outputs in `out/<name>/`. Check that the numbers make physical
   sense: signs, units and order of magnitude.
7. Put the result in `README.md`. Use `out:` markers for numbers, tables,
   figures and provenance, so the next run updates them. See
   [studies.md](../../studies.md#numbers-from-the-run). Check that the text
   still matches the new numbers.
8. Commit `README.md`, `study.yml`, `run.py`, any helper modules or extra
   config files, and at most two small figures. Do not commit `out/`. Do
   not add tests to the study folder.

## Checks before you finish

- `make test` passes.
- `make study S=<name>` runs from a clean `out/`.
- The README gives numbers with units and says how sure you are.
- The README does not say more than the result shows.
