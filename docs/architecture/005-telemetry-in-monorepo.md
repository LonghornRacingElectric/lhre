# ADR-005: Telemetry migrates into this repo, Rust via rules_rust

- **Status:** Accepted
- **Date:** 2026-08

## Context

The 2026 telemetry system lived in the `lhre-2026` repo: BEVO (the on-car
Rust daemons + React dash), a server-side Python/Kafka stack, and a shared
CAN schema (`drivers/longhorn-lib`). BEVO's Bazel files there were
commented-out stubs — it really built with Cargo, and the repos were
drifting apart: the CAN schema BEVO decodes is generated from the same CSVs
the firmware here consumes, synced by hand-run scripts and a CI drift
check.

## Decision

Telemetry moves into this monorepo incrementally, BEVO first, under
`apps/BEVO/` (the name is load-bearing — scripts, systemd units, and team
vocabulary all say BEVO):

- **Rust builds with `rules_rust` + crate_universe.** `Cargo.toml` +
  `Cargo.lock` stay authoritative for dependencies (one lockfile drives
  both Bazel and cargo/rust-analyzer). Protobuf codegen keeps the existing
  `build.rs`/prost flow, run hermetically by `cargo_build_script` against
  the hermetic protoc.
- **JS frontends and Pi deployment scripts are explicitly not Bazel-built.**
  React kiosk UI stays `npm`; systemd/kiosk setup stays shell. Bazel earns
  its keep on things CI must reproduce, not on deployment glue.
- **The CAN schema's generated artifacts are checked in** under
  `apps/BEVO/nonhermetic/assets/`; the generator (CSV source of truth)
  stays in lhre-2026 until a later phase unifies it with this repo's
  firmware CAN layer.

Later phases (server stack + OCI images, Pi aarch64 cross-compilation,
schema unification) get their own ADRs as they land.

## Alternatives considered

- **Keep telemetry in lhre-2026.** Rejected: perpetuates schema drift and a
  second build system; ADR-001 says one repo for the current car.
- **Rename BEVO to `onboard/`.** Rejected: churns every deploy script and
  the team's vocabulary for zero technical gain.
- **Port codegen to `rust_prost_library` now.** Rejected for this phase:
  the build.rs flow is what Cargo users run daily; keeping one codegen path
  beats maintaining two while both build systems are in use.
- **Vendor crates instead of crate_universe.** Rejected: tens of vendored
  crates in-tree for no hermeticity gain over a pinned `Cargo.lock`.

## Consequences

- `bazel test //...` now covers the telemetry daemons on every platform
  (cand's SocketCAN path is Linux-only; other OSes build a stub).
- Dependency edits require a repin
  (`CARGO_BAZEL_REPIN=1 bazel build //apps/BEVO/...`) and committing both
  lockfiles.
- Schema regeneration temporarily requires an lhre-2026 checkout (see
  `apps/BEVO/nonhermetic/README.md`).
