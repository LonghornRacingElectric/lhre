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
make test    # check that BobSim loads //vehicle
```

## Targets

| Target | What it does |
| ------ | ------------ |
| `make init` | Get the submodules and build the image |
| `make build` | Rebuild the image, for example after a BobSim bump |
| `make shell` | Open a shell in the container |
| `make test` | Check that BobSim loads `vehicle/vehicle.yml` and its tires |
| `make records` | Write `vehicle/vehicle.yml` into BobLib's Modelica records |
| `make records-undo` | Put BobLib's records back |
| `make bobsim T=<target>` | Run a BobSim make target. `T=help` lists them. |
| `make study S=<name>` | Run `studies/<name>/run.py`. Write outputs to `out/<name>/`. |
| `make bump-bobsim REF=<ref>` | Move the BobSim pin to a commit, tag or branch |
| `make clean` | Delete `out/` |

## Layout

```text
simulation/
  Makefile            # every target runs in Docker
  docker-compose.yml  # the image is built from bobsim/Dockerfile
  bobsim/             # submodule, pinned SHA
  studies/<name>/     # README.md + run.py
  tools/              # make test and study provenance
  docs/               # detail and agent skills
  out/                # gitignored outputs
```

## The container

- It mounts the repo root at `/lhre`. The working directory is
  `/lhre/simulation`.
- `BOBSIM_VEHICLE=/lhre/vehicle/vehicle.yml` and
  `PYTHONPATH=/lhre/simulation/bobsim`.
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

More detail is in [docs/](docs/README.md).
