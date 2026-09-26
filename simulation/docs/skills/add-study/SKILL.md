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

1. Make the folder `simulation/studies/<name>/`. Use a short kebab-case
   name that says the question.
2. Write `run.py`:
   - Read the vehicle path from `os.environ["BOBSIM_VEHICLE"]`.
   - Write every output to `os.environ["OUT_DIR"]`.
   - To change a parameter, load the vehicle, change the value, and write
     the changed copy to `OUT_DIR`. Do not edit `vehicle/vehicle.yml`.
   - Import BobSim. Do not copy its code. Do not change BobSim.
   - If the study solves many independent cases, run them with
     `tools.parallel.map_cases`. See
     [studies.md](../../studies.md#parallel-cases).
3. Write `README.md` with four sections: Question, Method, Result,
   Provenance. See [studies.md](../../studies.md#readmemd).
4. Run the study from `simulation/`:

   ```bash
   make study S=<name>
   ```

5. Read the outputs in `out/<name>/`. Check that the numbers make physical
   sense: signs, units and order of magnitude.
6. Put the result in `README.md`. Copy `out/<name>/provenance.json` into
   the Provenance section.
7. Commit `README.md`, `run.py` and at most two small figures. Do not
   commit `out/`.

## Checks before you finish

- `make test` passes.
- `make study S=<name>` runs from a clean `out/`.
- The README gives numbers with units and says how sure you are.
- The README does not say more than the result shows.
