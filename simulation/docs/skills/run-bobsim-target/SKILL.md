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
3. Run the target on our car:

   ```bash
   make car T=<target>
   ```

   `make car` writes our records, installs `vehicle/vehicle.yml` as
   `bobsim/vehicle.yml`, runs the target and restores `bobsim/`. See
   [bobsim.md](../../bobsim.md#run-a-bobsim-target-on-our-car). Some
   targets take up to two hours. Run them in the background. To run a
   candidate copy, add `VEHICLE=out/<name>/vehicle.yml`. See
   [bobsim.md](../../bobsim.md#run-a-bobsim-target-on-a-candidate).
4. For FourPost, check the outputs:

   ```bash
   make four-post-acceptance T=standard-eval-four-post
   ```

   Report each `UNAVAILABLE` check with the result. It is not a pass.
5. Find the outputs in `out/car/<target>/`. `identity.json` names the
   commits, the vehicle sha256 and the image. Do not commit them.

## Limits

- `make bobsim T=<target>` runs BobSim's own car, not ours. Use it only
  for BobSim's car. Tell the user which car gave a result.
- `make car` fails if `bobsim/` has local changes. If an earlier run
  stopped early, run `git -C bobsim checkout -- vehicle.yml` and
  `make records-undo`.
- Say which BobSim SHA gave the result. It is in `identity.json`.
