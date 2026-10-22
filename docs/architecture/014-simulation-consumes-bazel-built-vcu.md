# ADR-014: Simulation runs the Bazel-built VCU firmware as a shared library

- **Status:** Accepted
- **Date:** 2026-10

## Context

The goal is for a change to the VCU logic in `boards/VCU` to show up in a
BobSim full-vehicle run, and later in CI. Before this ADR, BobLib's
Modelica `StandardVCU` drove the simulated car, so a firmware change had
no effect on a simulation.

The constraints:

- [ADR-012](012-simulation-outside-bazel.md) keeps `simulation/` outside
  Bazel. The firmware is built only by Bazel.
- `boards/VCU/App` already runs on a host through LHAL fakes
  (`:vcu_app_test`), with no scheduler and a virtual clock.
- BobLib is GPL-3.0 and lhre is MIT. lhre does not copy BobSim or BobLib
  code.
- BobLib's `VehicleSim` declares its VCU as
  `replaceable StandardVCU vcu`, so a subclass can replace it without an
  edit to BobLib.
- A full-vehicle OMC build takes minutes. A firmware change should not
  need one.

## Decision

- Bazel builds `//boards/VCU:libvcu_sil.so`, a host shared library. It is
  the unmodified `:vcu_app` with a small C ABI (`vcu_sil_create`,
  `vcu_sil_step`, `vcu_sil_destroy`). Only those three symbols are
  exported. libc++ and FreeRTOS are linked in statically.
- The shim drives the App through the car's own LHAL and CAN interfaces,
  the same way `:vcu_app_test` does. The App does not know that it is in a
  simulation.
- `simulation/` uses the library as a binary. `make vcu-sil` runs
  `bazel build` on the host and copies the `.so` into the Modelica package
  `simulation/modelica/LhreVcu`. No Bazel runs in the container, and no
  firmware source is compiled there.
- `LhreVcu.FwVCU` extends `StandardVCU` and sets its torque request from
  the library at fixed sample events. BobLib's driver model and
  `VCUCore` limits stay. `LhreVcu.VehicleSimFw` replaces the `vcu` of
  `VehicleSim` with `FwVCU`.
- The OMC executable loads the library when it starts. A firmware change
  needs `make vcu-sil sil` only, not an OMC rebuild.
- The OMC executable links code that OMC generates from GPL-3.0 BobLib.
  lhre does not distribute it. It is a local and CI build output.

## Alternatives considered

- **Compile the firmware sources in the simulation container.** It copies
  the build into a second, non-hermetic toolchain, which can drift from
  the firmware build.
- **Export the firmware as an FMU.** It needs FMI glue and a second
  co-simulation master. The ABI here has three functions.
- **Run the firmware as a separate process (socket or CAN bridge).** It
  adds a time-sync protocol and makes the runs non-deterministic.
- **Port the control logic to Modelica.** Then the simulation tests a
  copy of the logic, not the firmware.
- **Patch `StandardVCU` in BobLib.** It changes BobSim for one lhre need,
  and BobLib is GPL-3.0. A subclass in lhre needs no BobLib change.

## Consequences

- A change to `App/` reaches the simulation through the same LHAL and CAN
  boundary as on the car.
- The simulated command updates only when the App sends a frame. Logic
  that runs only in a FreeRTOS task body is not seen, because the
  simulation calls `App::Step()`.
- Today the App has no control law and commands zero torque. The
  simulation proves the connection only. When the control law lands, the
  mapping in `boards/VCU/Sil/vcu_sil.cpp` changes. The ABI and the Modelica
  package stay.
- The ABI is a contract between `boards/VCU/Sil/vcu_sil.h` and
  `simulation/modelica/LhreVcu/package.mo`. Change both in the same PR.
- The library is Linux-only. Windows users use WSL or a CI artifact.
- `VehicleSimFw` writes the `vcu` bindings of BobLib again. Check them
  after each BobSim pin bump.
- A CI job can cache the OMC build on the BobSim pin, the vehicle and
  `LhreVcu`, and rebuild only the library for a firmware change.
