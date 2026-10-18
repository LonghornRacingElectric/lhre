# ADR-012: Simulation runs BobSim in Docker, outside Bazel

- **Status:** Accepted
- **Date:** 2026-09

## Context

Vehicle dynamics studies for the car lived in
[lhre-simulation](https://github.com/LonghornRacingElectric/lhre-simulation).
That repo pinned BobSim as a submodule, but the pin went stale and each
study copied its own BobSim glue. ADR-001 says the current car has one
repo, so the studies and the vehicle data belong here.

[BobSim](https://github.com/BobDyn/BobSim) is the runtime. Its high-fidelity
tier compiles BobLib Modelica models with the OpenModelica compiler and
`-march=native`. That is not hermetic, it does not run on Windows without
Docker, and a run takes minutes to hours. Presubmit runs `bazel test //...`
on Linux and Windows for every PR. BobSim and BobLib are GPL-3.0. lhre is
MIT.

## Decision

- `simulation/` is not a Bazel package tree. It is in `.bazelignore`.
- BobSim is a git submodule at `simulation/bobsim`, pinned to one commit.
  lhre does not copy BobSim code.
- `make` and Docker are required in `simulation/`. Every target runs in an
  image built from BobSim's Dockerfile.
- The car's parameters live in `vehicle/`, a normal Bazel package that
  exports `vehicle.yml` and the tire files. Simulation reads it through the
  `BOBSIM_VEHICLE` environment variable. Other Bazel targets can depend on
  it.
- A study is `simulation/studies/<name>/` with a `README.md` and a
  `run.py`. Outputs are not committed.
- Its CI is `.github/workflows/simulation.yml`, path-filtered and not a
  required check. Presubmit skips the Bazel jobs when a PR touches only
  `autonomy/` or `simulation/`.

## Alternatives considered

- **Build simulation with Bazel.** The OpenModelica toolchain is not
  hermetic, and Windows presubmit would need platform gating on every
  target. Every PR would pay for runs that only simulation changes need.
- **Vendor BobSim into lhre.** It brings GPL-3.0 code into an MIT repo, and
  the copy drifts from BobSim.
- **Install BobSim as a Python package.** BobSim is not packaged, and its
  Modelica tier needs OpenModelica anyway. Docker gives both.
- **Keep lhre-simulation.** It already drifted from BobSim and from the car
  data in this repo.

## Consequences

- `bazel test //...` neither builds nor tests simulation. `make test` in
  its own workflow does.
- Simulation contributors need `make` and Docker, including on Windows.
  `make init` checks out the submodules with LF endings and long paths on.
- A BobSim change lands in BobSim first. A lhre PR then moves the pin.
- `vehicle/` is the one place for vehicle numbers. Autonomy still keeps its
  own copy in `lhr_vehicle/config/vehicle.yaml`. Moving it to `//vehicle`
  is a follow-up.
- Root `AGENTS.md` build instructions do not apply inside `simulation/`.
  `simulation/AGENTS.md` does.
