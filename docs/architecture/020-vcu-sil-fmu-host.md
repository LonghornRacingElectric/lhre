# ADR-020: VCU SIL steps the firmware as a library beside a car-model FMU

- **Status:** Accepted
- **Date:** 2026-11

## Context

The VCU team wants to test control changes against BobLib's full vehicle
before track time: software-in-the-loop (SIL). The firmware (`vcu::App`)
already runs on the host against LHAL fakes and the test bench
(`boards/VCU/App/test/bench.hpp`). The car model is `OrionTorqueFMI`:
BobLib's `VehicleFMI` with its VCU replaced by one that takes a motor torque
command instead of pedals, so the firmware, not the model, owns the
pedal-to-torque path. It is built by hand with BobLib's exporter, not by
`make fmu` (which builds the pedal-input `OrionFMI`), and its build still
needs two OpenModelica runtime fixes that ADR-016 wants upstream.

Constraints:

- The firmware must run unmodified, through the same LHAL and CAN boundary
  as on the car.
- BobLib is GPL and lhre is MIT. [ADR-016](016-fmu-artifact-boundary.md)
  keeps model source and compiled FMUs out of the tree, and the FMU must
  match the runtime CPU.
- `:vcu_app` is private to `//boards/VCU`, and anything under `App/` is
  compiled into the firmware.
- A control change should be testable in seconds, many times a day.

An earlier attempt (unmerged branch `abatra/vcu-traction-control`, with its
own draft "ADR-014") loaded a firmware library into the Modelica model and
called it at sample events. On its author's machine a 5 s coast took 232 s
at a 10 ms tick. The solver called the library 2 to 40 times per 3 ms tick,
and pedal and motor speed reached the app through a seam (`SetDriveInputs`)
that the car does not have.

## Decision

- `boards/VCU/Sil/` holds a C++ rig (`vcu::sil::Rig`) that owns the bench
  and the app. It turns physical values into the ADC voltages, GPIO levels
  and CAN frames the car would produce, at each message's spec rate, and
  decodes the app's `INVERTER_TORQUE_COMMAND` off the bus. Its targets are
  in `boards/VCU/BUILD.bazel`, outside `App/`, so nothing reaches the
  firmware.
- Bazel builds the rig into `libvcu_sil.so` behind a small, versioned C ABI
  that exports only `vcu_sil_*`.
- A Python host (`boards/VCU/Sil/host/`, outside Bazel) loads the library
  with ctypes and steps the FMU with fmpy, one 3 ms control tick at a time:
  read the model, advance the firmware three 1 ms passes, apply its torque
  command, step the model.
- The FMU stays outside the repo. The host takes its path, checks its
  interface, CPU and provenance manifest, and records whether it was built
  from the checkout's `vehicle.yml` and BobSim pin.
- `boards/VCU/Sil/run.sh` runs all of it in a pinned dev container (Bazelisk,
  Python, fmpy), with the repo mounted read-only.

## Alternatives considered

- **Firmware library inside the Modelica model** (the earlier attempt).
  Measured on one Mac at a 3 ms tick with a stub firmware: with that
  branch's solver settings a 3 s launch took 72 to 90 s and a 5 s coast 104
  to 107 s. OpenModelica's `-noRestart` brought a smooth command down to 4.4
  and 3.0 s, but a command that changes every tick still took 22.7 to 62.1 s.
  An FMU host (the pedal FMU, same stub) took 1.7 to 9.7 s for the launches
  and 2.6 to 3.1 s for the coasts. Either way the solver re-enters the
  library during event iteration, 2 to 40 times per tick, which the car
  never does; the FMU host calls the firmware exactly once per tick.
- **Pedal-input FMU:** BobLib's own VCU would sit between the firmware and
  the motor, so the firmware's torque would not be what the car applies.
- **Separate processes over a virtual CAN bus:** the CAN boundary is the
  same either way, but two clocks need synchronizing and runs stop being
  deterministic. Kept for hardware-in-the-loop, where the clock is real.
- **A C++ host loading the FMU:** one binary, but the FMI glue, FMU
  unpacking and plotting are already solved in Python (fmpy, used by
  `lhr_sim_fmu`), and the scenario and analysis code stays easy to change.

## Consequences

- A firmware change shows up in a vehicle run after a Bazel rebuild of the
  library; the FMU is untouched.
- The rig's tests run in the normal Bazel CI (Linux, Windows, UBSan). The
  Python host's tests are not in CI yet. Most of them, including the ctypes
  layout check, need only `libvcu_sil.so`; the end-to-end ones need the FMU,
  which stays local until a CI job can build it.
- The C ABI is a contract between `vcu_sil.h` and `host/vcusil/firmware.py`.
  A layout table makes a missed field fail at load, but meaning changes need
  a version bump.
- The library and the GPL FMU share a process at run time, as `lhr_sim_fmu`
  already does under ADR-016. Neither is distributed with the other.
- Sensor models (calibration inverses, wheel speed as a magnitude, the
  hard-brake comparator) live in the rig, never in `App/`.
