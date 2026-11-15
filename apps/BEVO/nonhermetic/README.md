# BEVO Nonhermetic (Local/Embedded) Runbook

Runtime scripts for already-built BEVO executables on the Pi. Build the core
`cand`, `loggerd`, and `publishd` executables through
`bazel build //apps/BEVO:pi_core_binaries`. These scripts configure real CAN
interfaces and GPIO; they do not compile Rust or generate schemas.

## Files

- `assets/` — **generated, gitignored**: deployed runtime `can.json` from
  [`lib/spec`](../../../lib/spec/README.md). Bazel bundles include this asset.
- `run_mock_stack.sh` — run `cand + dashd + loggerd + mock_can` locally
- `run_full_mock_stack.sh` — the complete stack incl. `publishd`, mock CAN
- `run_real_stack.sh` — `publishd + cand + dashd + loggerd` on real CAN
- `bevo_telemetry.service` — systemd unit (installed by
  `../dashd/deploy/install.sh` for checkouts, or the one-liner in
  [../README.md](../README.md) for bundle deploys)
- `start_telemetry.sh` — what that unit runs

The `run_*` scripts use the deployed `bin/` directory;
`BEVO_BIN_DIR` overrides its location. The older CSV-generation and Cargo
setup helpers were removed. Dashboard and mock-stack runtime helpers still
require their additional executables to be installed separately.

## Prerequisites

- Bazel on the build machine; deployed executables need no build toolchain.
- Optional for `cell.py`: install `../requirements.txt` (Pi-only deps)
