---
name: run-bobsim-target
description: Run a BobSim make target (standard evaluation, envelope, lap sim, sweep) from simulation/ in Docker. Use when someone asks for a GGV, YMD, lap time, steady-state or transient result.
---

# Run a BobSim target

## Procedure

1. From `simulation/`, list the targets:

   ```bash
   make bobsim T=help
   ```

2. Pick the target. See the tier table in [bobsim.md](../../bobsim.md#tiers).
3. If the target is in the high-fidelity tier, write our vehicle into the
   Modelica records, then build:

   ```bash
   make records
   make bobsim T=standard-build
   ```

4. Run the target:

   ```bash
   make bobsim T=<target>
   ```

   Some targets take more than 15 minutes. Run them in the background.
5. Find the outputs. BobSim writes them inside `bobsim/`, for example
   `bobsim/_3_StandardSim/generated_results/`. Copy what you need to
   `out/`. Do not commit them.

## Limits

- BobSim targets use `bobsim/vehicle.yml`, not `vehicle/vehicle.yml`, until
  BobSim reads `BOBSIM_VEHICLE`. Tell the user this when you give a result.
  For a result on our vehicle, write a study instead. See
  [add-study](../add-study/SKILL.md).
- `make records` changes files inside the submodule. Run
  `make records-undo` before a BobSim bump.
- Say which BobSim SHA gave the result: `git -C bobsim rev-parse --short HEAD`.
