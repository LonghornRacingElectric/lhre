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
| `make records` | Write `vehicle/vehicle.yml` into BobLib's Modelica records. `VEHICLE=<path>` writes a candidate copy instead. |
| `make records-undo` | Put back only the files `make records` wrote |
| `make bobsim T=<target>` | Run a BobSim make target on BobSim's own car. `T=help` lists them. |
| `make car T=<target>` | Run a BobSim make target on `vehicle/vehicle.yml`, then restore `bobsim/`. Outputs and `identity.json` go to `out/car/<target>/`. `VEHICLE=out/<name>/vehicle.yml` runs a candidate copy to `out/car/<target>--<name>/`. See [docs/bobsim.md](docs/bobsim.md#run-a-bobsim-target-on-our-car). |
| `make four-post-acceptance T=<target>` | Check the FourPost outputs of `make car T=<target>`. `T` defaults to `standard-eval-four-post`. Pass the same `VEHICLE` as `make car`. |
| `make study S=<name>` | Run `studies/<name>/run.py`. If it succeeds, write outputs to `out/<name>/`. `STUDY_WORKERS=n` limits the CPUs for parallel cases. `ARGS=... V=<name>` runs a variant to `out/<name>--<V>/`. |
| `make stale S=<name>` | Check if the result in `studies/<name>/README.md` is current, stale or unknown. Without `S`, check every study. See [docs/studies.md](docs/studies.md#check-a-result). |
| `make bump-bobsim REF=<ref>` | Move the BobSim pin to a commit, tag or branch |
| `make clean` | Delete `out/` |

## Layout

```text
simulation/
  Makefile            # every target runs in Docker
  docker-compose.yml  # the image is built from bobsim/Dockerfile
  bobsim/             # submodule, pinned SHA
  studies/<name>/     # README.md + run.py
  tools/              # make smoke, make car, study provenance, README fill and shared study helpers
  tools/              # make smoke, vehicle and bundle checks, anti records, study helpers
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
  BobSim's own `vehicle.yml`. `make car T=...` installs our vehicle for
  one run.
- The Modelica tier compiles BobLib's `.mo` records, not `vehicle.yml`.
  `make car` writes our vehicle into them and puts them back after the
  run. `make records` and `make records-undo` do the two steps by hand.
- BobSim targets write results inside `bobsim/`. BobSim's `.gitignore`
  covers them.

More detail is in [docs/](docs/README.md).
