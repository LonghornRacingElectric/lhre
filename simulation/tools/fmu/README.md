# tools/fmu

`make fmu` builds `out/fmu/OrionFMI.fmu`: BobLib's full vehicle model
(`BobLib.Experiments.Standards.VehicleFMI`) as an FMI 2.0 FMU with CVODE
inside. `lhr_sim_bobsim` loads it with `plant:=fmu`. The FMU is a
platform-specific binary and GPL-covered through BobLib, so it stays in the
gitignored `out/`. Build it on the machine that runs it. It takes about
6 minutes.

The vehicle is [`vehicle/vehicle.yml`](../../../vehicle/README.md). The
build does not use the record that is checked in to BobLib, and it does not
need `make records`. Build the FMU again after you change `vehicle.yml`.

The numbers below come from BobSim `4da577a`, OpenModelica 1.26.3 and
`vehicle.yml`, on an Apple Silicon laptop in Docker.

## What build.sh does

1. Copy BobLib from the submodule working tree to `out/fmu/build/BobLib`.
   The submodule does not change. BobLib is in `engines/boblib` in current
   BobSim and in `_0_Utils/external/BobLib` in older pins; the build finds
   either, and also imports the record generator from `app/` or `_5_App/`.
2. Run `records.py`. It calls BobSim's record generator, the one that
   `make records` uses, to write the records of `vehicle.yml` into the copy.
   It also writes the model `OrionFMI`: BobLib's `VehicleFMI` with
   `brake.front_bias` from `vehicle.yml`, because BobSim's records do not
   carry the brake bias. BobSim writes the records where `paths.boblib`
   points, so `records.py` gives it a copy of `vehicle.yml` with that path
   set to the copy of BobLib. BobLib's `VehicleFMI` uses one record and its
   axle models by name. Keep `vehicle.name` and `architecture` as they are:
   `records.py` stops the build if the name changes, and `omc` stops it if
   the architecture changes.
3. Run `omc orion_fmi.mos` to generate a source-only FMU. The script uses
   the compiler options BobSim uses for `VehicleSim`, and `headless = true`.
   `--fmiFilter=internal` also exports protected variables, for example the
   force of each tire, which `lhr_sim_bobsim` draws. The FMU then has about
   28,000 variables and 6 MB.
4. Run `split_init.py` and `patch_runtime.py` on the generated sources.
5. Build the FMU's CMake project with clang, and zip the result.
6. Write `out/fmu/OrionFMI.json` next to the FMU: the BobSim SHA, the
   hashes of `vehicle.yml` and of the FMU, the OpenModelica version and the
   platform. A consumer can check that the FMU matches the current
   `vehicle.yml`.

`patch_runtime.py` stops the build if the code it edits changes. Then check
whether OpenModelica fixed the problem.

| Script | Why |
| --- | --- |
| `split_init.py` | OpenModelica writes the variable table as one 287k-line function. clang needs hours to compile it at `-O3`. Split into 10k-line functions, it compiles in about a minute |
| `patch_runtime.py` | Fixes to OpenModelica 1.26's FMU runtime, in the next table |

| File | Fix | Why |
| --- | --- | --- |
| `fmu2_model_interface.c` | Skip `overwriteOldSimulationData` in `updateIfNeeded` for co-simulation | The first `fmi2GetReal` after each step filled the solver history with the current point. With reads every step, the launch from a standstill failed |
| `fmu2_model_interface.c` | Save and restore the memory pool around the CVODE step and the event iteration in `fmi2DoStep` | The runtime did not free the temporary memory of these model calls. The process grew by about 70 MB/s, and the runtime called `abort()` about 42 s after each start |
| `cvode_solver.c` | Return 1, not -1, when a model call fails inside CVODE | CVODE treated a failed nonlinear system in a trial step as fatal. A positive value makes it retry with a smaller step |

These fixes are candidates for OpenModelica issues.
