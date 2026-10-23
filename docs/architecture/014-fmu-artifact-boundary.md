# ADR-014: Build an identified FMU artifact for the autonomy plant

- **Status:** Accepted
- **Date:** 2026-10

## Context

Autonomy needs to step BobLib's full vehicle from external driver commands.
BobLib merged the FMI exporter in commit `4741a5e`. BobSim's latest commit
still pins an older BobLib commit. Their `BobLib/` model trees are identical;
the exporter and tests differ. Editing the nested dependency would leave
BobSim dirty and make existing simulation results ambiguous.

## Decision

Keep the BobSim and nested BobLib pins unchanged. `make fmu-source` obtains
the reviewed BobLib exporter at an exact commit under ignored `simulation/out/`.
`make fmu` copies its model tree into temporary build space. It uses BobSim's
public Modelica generation plan and the canonical vehicle/tire files there.
It calls the upstream exporter and keeps its runtime corrections upstream.
No model source or compiled FMU enters the MIT source tree.

The output is an FMU and a provenance manifest. The ROS plant verifies the
FMU bytes, canonical vehicle/tire hashes and BobSim calibration commit before
loading it. It publishes the model's exported rear-axle pose and body twist.
The adapter maps speed and road-wheel commands to driver pedals and handwheel
angle. It does not replace velocity or integrate a substitute trajectory.

## Alternatives considered

- Wait for a BobSim pin update. This delays an already available exporter.
- Edit the nested BobLib checkout. This breaks the clean dependency boundary.
- Vendor exporter corrections. This duplicates upstream solver work and code.
- Keep the three-DOF plant only. It remains useful, but does not exercise the
  full Modelica vehicle.

## Consequences

The FMU has its own explicit source identity until BobSim adopts the exporter.
Builds need Linux OpenModelica and the artifact must match the runtime CPU.
The model's known stop/relaunch limitation remains an upstream issue; a failed
FMI step stops the simulator instead of publishing invented state or time.
The FMU plant is optional. The Gazebo MVP criteria remain unchanged.
