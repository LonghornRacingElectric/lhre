# BEVO Nonhermetic (Local/Embedded) Runbook

Non-Bazel runtime for the BEVO daemons: Cargo-built binaries plus the
scripts that run them on the Pi. The Bazel flow (`//apps/BEVO/...`) is the
default for CI and development; this exists for embedded deployment and
quick local iteration. "Nonhermetic" means exactly that: these scripts use
whatever `python3`/`cargo`/`ip` the machine has, touch real CAN interfaces
and GPIO, and their results depend on the machine — the opposite of the
Bazel builds, which are reproducible everywhere by construction.

## Files

- `assets/` — **generated, gitignored**: `sync_assets.sh` writes the
  runtime `can.json` here from [../schema/](../schema/README.md) (deployed
  bundles ship it pre-generated at this path)
- `sync_assets.sh` — regenerate `assets/can.json` (python3, stdlib only)
- `setup_local_env.sh` — `sync_assets.sh` + `cargo build --release`
- `run_mock_stack.sh` — run `cand + dashd + loggerd + mock_can` locally
- `run_full_mock_stack.sh` — the complete stack incl. `publishd`, mock CAN
- `run_real_stack.sh` — `publishd + cand + dashd + loggerd` on real CAN
- `bevo_telemetry.service` — systemd unit (installed by
  `../dashd/deploy/install.sh` for checkouts, or the one-liner in
  [../README.md](../README.md) for bundle deploys)
- `start_telemetry.sh` — what that unit runs

The `run_*` scripts look for binaries in `bin/` first (the deployed-bundle
layout — see [../README.md](../README.md) → "Deploying to the Pi"), then
fall back to Cargo's `target/release`; `BEVO_BIN_DIR` overrides both.

## Prerequisites

- `cargo` / Rust toolchain, `python3`, `protoc` (`PROTOC` env or PATH) —
  for building only; deployed bundles need none of these
- Optional for `cell.py`: install `../requirements.txt` (Pi-only deps)
