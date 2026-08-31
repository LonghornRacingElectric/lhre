# ADR-006: The Pi gets binaries, not the repo

- **Status:** Accepted
- **Date:** 2026-08

## Context

The monorepo (toolchains, boards, docs, external deps) is far too heavy to
clone onto the on-car Raspberry Pi, and building Rust there is slow. The
old flow was `git pull && cargo build --release` on the Pi, which needed a
checkout, a Rust toolchain, and patience.

## Decision

Deploys ship a self-contained binary bundle; the Pi never sees the repo or
a toolchain:

- The BEVO daemons cross-compile to **static aarch64-musl** ELFs via
  `//platforms:pi`. Static musl means zero coupling to the Pi image's glibc
  — any 64-bit Pi OS runs them.
- `//apps/BEVO:pi_bundle` packs the binaries (`bin/`), the CAN schema
  assets, and the runbook scripts (~8 MB tar.gz). The runbooks prefer
  `bin/` when present, so the same scripts drive both bundle deployments
  and Cargo checkouts.
- `bazel run //apps/BEVO:deploy -- [user@host] [dest]` pushes the bundle
  over SSH and restarts the systemd service if installed.

## Alternatives considered

- **Sparse checkout + cargo on the Pi** — works (BEVO's Cargo build is
  self-contained; documented as the escape hatch) but keeps a toolchain and
  slow builds in the race-day loop.
- **Dynamic glibc binaries (`aarch64-unknown-linux-gnu`)** — glibc version
  skew between the cross toolchain and whatever image the Pi runs is a
  classic silent breakage; static musl deletes the problem for a few
  hundred kB per binary.
- **Container images** — heavier runtime, no benefit for a single fixed
  device that also drives a kiosk display.

## Consequences

- Deploying requires a dev machine (or CI) with the repo, not a Pi with the
  repo. Rollback = redeploy an older bundle.
- The kiosk frontend (npm) still uses its own deploy flow in
  `apps/BEVO/dashd/deploy/`.
- If a daemon ever gains a C dependency, it must build against musl (or the
  triple decision gets revisited in a new ADR).
