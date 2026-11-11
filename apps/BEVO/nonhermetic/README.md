# BEVO Nonhermetic (Local/Embedded) Runbook

Runtime scripts for already-built BEVO executables on the Pi. Build the core
`cand`, `loggerd`, and `publishd` executables through
`bazel build //apps/BEVO:pi_core_binaries`. These scripts configure real CAN
interfaces and GPIO; they do not compile Rust or generate schemas.

## Files

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

For the [separate updater](../updater/README.md), set
`BEVO_CORE_ROOT=/opt/bevo/core`. `run_real_stack.sh` resolves its `current`
symlink once at startup and launches core executables from that release;
the dashboard remains separately installed under `BEVO_BIN_DIR`.
`BEVO_CORE_RELEASE=/absolute/release` is an explicit admin override and takes
precedence over `BEVO_CORE_ROOT`. Missing explicitly selected releases fail
startup rather than falling back. A core release is required to start the real stack. Only publishing requires
`reconciliation.json`; acquisition and logging remain independent of its validity. The launcher exports its resolved
physical directory as `BEVO_CORE_RELEASE_ROOT` for `publishd`.
The outer `start_telemetry.sh` powers GPIO/modem hardware and configures CAN
before invoking updater startup synchronization. Network failures preserve a
verified installed activation after a bounded wait. The inner launcher resolves
that activation once. Configure the URL/root/updater path in
`/etc/default/bevo-telemetry`. Running stacks only check for updates every five
minutes; activation waits for the next coordinated startup.

For mock publishing, set `BEVO_CORE_RELEASE_ROOT` to a plan for the same
Artemis schema compiled into its installed mock executables.
An explicit updater admin override can prepare an offline identity plan;
the mock launcher does not invent a plan or bypass publisher validation.

The CAN mapping and CSV column layout are compiled from the shared textproto
at build time. Acquisition and logging do not read runtime `can.json`; the Python mock traffic
generator uses the current Bazel-generated CAN JSON. The old
`CAND_CAN_JSON_PATH` / `LOGGERD_CAN_JSON_PATH` settings no longer select schemas.
To change a schema, install the matching rebuilt executable release.

## Prerequisites

- Bazel on the build machine; deployed executables need no build toolchain.
- Optional for `cell.py`: install `../requirements.txt` (Pi-only deps)

`bazel test --config=local //apps/BEVO:startup_test` verifies modem-before-sync
and release selection before launching the core, with stubbed hardware commands.
