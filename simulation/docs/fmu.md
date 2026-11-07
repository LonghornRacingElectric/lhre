# Orion FMU artifact

`make fmu` builds Orion's driver-input-only full vehicle for the
[autonomy FMU plant](../../autonomy/ros2/src/lhr_sim_fmu/README.md).
Run it from `simulation/`. Use `make build` first if the image is missing.
The output stays in ignored `out/fmu/`.

## Source and build

`make fmu-source` clones BobLib into `out/fmu/boblib-source` and checks out
`4741a5ec6201635051031ed356a1bb55932df89c`, the merged VehicleFMI exporter.
This step needs GitHub access. It refuses local source changes. The subsequent
container build has no network, as with the rest of simulation. The build
refuses a modified BobSim checkout or a different upstream Modelica tree,
so a future dependency update needs review instead of silently mixing models.

The export pin matched BobSim `22c5056` and its nested BobLib `1bd6d78`.
Both `BobLib/` model trees were Git tree
`bb8379ca9fe8cbae57a47d3af441d40110d839ba`. BobSim `f58f932` moves the
nested BobLib to `75b6e03`, which has another model tree and nested
records. So `make fmu` refuses at this pin until a reviewed exporter
commit moves `FMU_BOBLIB_SHA`.

The build calls public `app.modelica_generator.plan_modelica_stack` on
`vehicle/vehicle.yml` with the pinned BobSim root. It writes the generated
records and package orders only into a temporary copy of BobLib. Tire data
comes from the canonical vehicle directory. It creates a small Modelica
binding wrapper for driver commands and rear-axle outputs, then calls the
upstream `Tests/modelica_fmu_export.py` public `export_fmu` function.
It does not copy or reimplement upstream compiler/runtime corrections.

The exporter uses OpenModelica 1.27.1, CMake and the container's C compiler.
It produces FMI 2.0 co-simulation with CVODE, not fixed-step Euler. Each build
stage has the upstream 3600 s timeout. Output includes:

- `OrionFMI.fmu`, compiled for the build container's Linux CPU architecture.
- `provenance.json`, schema `lhre.fmu.v1`, with artifact, canonical vehicle,
  tire, generated-record and wrapper hashes plus BobSim and BobLib commits.

The loader checks identities before executing native code. An old
`VehicleSim.fmu` cannot be substituted: it has a different public interface
and may retain the built-in controller. Rebuild after vehicle, tire or
calibration-pin changes. The source checkout and installed nested dependency
remain clean; generated records need no `make records-undo` for this target.

## Validation

The actual native test and closed-loop commands are in the
[plant README](../../autonomy/ros2/src/lhr_sim_fmu/README.md). These tests run
in the ROS container with FMPy. `make test` still runs the normal simulation
smoke and unit suite; it does not compile or drive an FMU on every PR.

This build route implements the capability tracked by LHRe #98 without
waiting for BobSim to adopt the merged exporter. It does not change or close
that PR. See [ADR-016](../../docs/architecture/016-fmu-artifact-boundary.md)
for the dependency boundary. BobLib's known motor-latch failure on some
brake-hold/relaunch profiles remains upstream; do not treat one passing
profile as a general solver or hardware acceptance result.
