# simulation

Vehicle dynamics studies for the current car. The runtime is
[BobSim](https://github.com/BobDyn/BobSim), pinned as a git submodule at
`simulation/bobsim`. BobSim brings the BobLib Modelica library with it.

This directory is outside Bazel
([ADR-012](../docs/architecture/012-simulation-outside-bazel.md)).
`bazel test //...` does not build or test anything here. Make and Docker do.

## Requirements

You need `make` and Docker on every OS. Every target runs in the container.

| OS | Install |
| -- | ------- |
| Linux | `sudo apt install make` and Docker Engine |
| macOS | `xcode-select --install` and Docker Desktop |
| Windows | `winget install ezwinports.make` and Docker Desktop. Run `make` from Git Bash. |

## Start

```bash
cd simulation
make init    # get BobSim and BobLib, build the image
make test    # check that BobSim loads //vehicle, then run the fast tests
```

## Targets

| Target | What it does |
| ------ | ------------ |
| `make init` | Get the submodules and build the image |
| `make build` | Rebuild the image, for example after a BobSim bump |
| `make shell` | Open a shell in the container |
| `make test` | Run `make smoke`, then `make unit` |
| `make smoke` | Check that BobSim loads `vehicle/vehicle.yml` and its tires. It checks loading only. |
| `make unit` | Run the fast unit and contract tests in `tests/`. Then check that `bobsim/` has no local changes. See [docs/tests.md](docs/tests.md). |
| `make records` | Write `vehicle/vehicle.yml` into BobLib's Modelica records |
| `make records-undo` | Put back only the files `make records` wrote |
| `make bobsim T=<target>` | Run a BobSim make target. `T=help` lists them. |
| `make study S=<name>` | Run `studies/<name>/run.py`. If it succeeds, write outputs to `out/<name>/`. `STUDY_WORKERS=n` limits the CPUs for parallel cases. `ARGS=... V=<name>` runs a variant to `out/<name>--<V>/`. |
| `make stale S=<name>` | Check if the result in `studies/<name>/README.md` is current, stale or unknown. Without `S`, check every study. See [docs/studies.md](docs/studies.md#check-a-result). |
| `make bump-bobsim REF=<ref>` | Move the BobSim pin to a commit, tag or branch |
| `make vcu-sil` | Build `//boards/VCU:libvcu_sil.so` with Bazel on the host. Copy it into `modelica/LhreVcu/Resources/Library/`. `BAZEL_ARGS=--config=local` builds without BuildBuddy. |
| `make bobsim-fw` | Build `LhreVcu.VehicleSimFw` with OMC: BobLib `VehicleSim` with the VCU firmware as its VCU. It runs `make records` first and puts the BobLib files back after. |
| `make sil TRACK=<name>` | Run `VehicleSimFw` on a driver trace, check the result and write `out/vcu-sil/<name>/report.pdf`. The default track is `coast`, 5 s from 15 m/s with no pedals. See [modelica/LhreVcu](modelica/LhreVcu/README.md#make-sil). |
| `make clean` | Delete `out/` |

## Layout

```text
simulation/
  Makefile            # every target runs in Docker
  docker-compose.yml  # the image is built from bobsim/Dockerfile
  bobsim/             # submodule, pinned SHA
  studies/<name>/     # README.md + run.py
  tools/              # make smoke, study provenance, README fill and shared study helpers
  modelica/LhreVcu/   # Modelica package that runs the VCU firmware in VehicleSim
  tests/              # make unit: fast unit and contract tests
  docs/               # detail and agent skills
  out/                # gitignored outputs
```

## The container

- It mounts the repo root at `/lhre`. The working directory is
  `/lhre/simulation`.
- `BOBSIM_VEHICLE=/lhre/vehicle/vehicle.yml` and
  `PYTHONPATH=/lhre/simulation/bobsim:/lhre/simulation`. Studies import
  BobSim modules and `tools.<module>`.
- It has no network (`network_mode: none`). Only the image build uses the
  network.

## Known limits

- BobSim does not read `BOBSIM_VEHICLE` yet. `make test` and studies pass
  the path to BobSim themselves. `make bobsim T=...` targets still use
  BobSim's own `vehicle.yml` until BobSim reads the variable.
- The Modelica tier compiles BobLib's `.mo` records, not `vehicle.yml`.
  Run `make records` before a Modelica target to write our vehicle into
  them. `make records-undo` puts them back.
- BobSim targets write results inside `bobsim/`. BobSim's `.gitignore`
  covers them.

## VCU firmware in the vehicle model

`make vcu-sil bobsim-fw sil` runs the VCU firmware from `boards/VCU` as the
VCU of BobLib `VehicleSim`. Bazel builds the firmware as a shared library.
The OMC executable loads it at run time. After a firmware change, run
`make vcu-sil sil`. You do not need to build the model again. See
[modelica/LhreVcu](modelica/LhreVcu/README.md) and
[ADR-014](../docs/architecture/014-simulation-consumes-bazel-built-vcu.md).

More detail is in [docs/](docs/README.md).
