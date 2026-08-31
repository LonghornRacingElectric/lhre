# ADR-010: Telemetry server stack builds in Bazel, its tests run beside Docker

- **Status:** Superseded by [ADR-011](011-bazel-owned-telemetry-images.md)
- **Date:** 2026-08

## Context

[ADR-005](005-telemetry-in-monorepo.md) moved BEVO into `apps/BEVO` and
left the server side of telemetry (MQTT ingest, Kafka bridge and
processors, Postgres, the analysis webtool) for a later phase. That tree
arrived in August 2026 as
[`apps/telemetry`](https://github.com/LonghornRacingElectric/lhre/tree/main/apps/telemetry)
with its history. In `lhre-2026` it already built with Bazel: Python
targets, `pkg_tar` + `rules_oci` container images, `compile_pip_requirements`
locks, and integration tests that expect Kafka, Postgres and MQTT to be
running. Its CI ran those tests on one Linux runner after
`docker compose up`, with credentials from repo secrets. This repo's
presubmit runs `bazel test //...` on Linux (BuildBuddy remote executors)
and on Windows, with no services and no secrets.

## Decision

- `apps/telemetry` is in the Bazel graph. Labels are `//apps/telemetry/...`.
  The Python packages get two import roots through `imports`
  (`apps/telemetry` for `stack.` and `analysis.`, `apps` for `telemetry.`),
  so the code itself did not change.
- Two pip hubs, `@telemetry_reqs` and `@telemetry_stack_reqs`, serving the
  committed locks under the repo's 3.12 interpreter. The locks were
  compiled on 3.11, but every pin has 3.12 wheels or is pure Python, so no
  second interpreter is registered. The production images still run
  3.11 (`python:3.11.4-slim`).
- Container images (`oci_image`) and the server-stack libraries are
  `target_compatible_with` Linux. The Windows presubmit job analyzes them and
  skips them. The base image is pulled by digest.
- Tests that need services are tagged `manual` (presubmit's `//...` never
  sees them) and `local` (they run on the machine that has Docker, never on
  a remote executor). They run in
  [`.github/workflows/telemetry.yml`](https://github.com/LonghornRacingElectric/lhre/blob/main/.github/workflows/telemetry.yml):
  path-filtered to `apps/telemetry/**`, Docker Compose on the runner,
  credentials from repo secrets, skipped while the secrets are absent.
  `//apps/telemetry:unit_tests` stays in presubmit.
- `protoc` for the generated Python module comes from `//tools/protoc`; the
  proto comes from `//apps/BEVO/schema` ([ADR-007](007-can-schema-in-repo.md):
  one schema source).
- Not Bazel-built, the same line ADR-005 drew: the Next.js webtool (`npm`),
  the Go Kafka bridge and Grafana plugin (Docker), Compose files, dev
  scripts.

## Alternatives considered

- **Run the integration tests in presubmit with service containers.**
  Presubmit executes tests on BuildBuddy; a `services:` block on the runner
  is invisible to them, and pinning those tests local would still put
  Docker Compose and secrets into the firmware presubmit. A separate
  workflow keeps the blast radius to telemetry PRs.
- **Register a 3.11 toolchain to match the locks.** Tried first; `//...`
  analyzes libraries in the default 3.12 configuration, where a 3.11-only
  hub has no wheel to offer, so every target would need version gating.
  Serving the locks under 3.12 needs nothing. Re-locking on 3.12 is the
  clean end state once the workflow is green.
- **Keep the stack in `.bazelignore` and build with Docker only.** Loses the
  hermetic image builds and the lock checks the old repo already had.

## Consequences

- `bazel test //...` on Linux builds four container images (cheap after the
  first run thanks to the remote cache) and runs the unit tests.
- A new telemetry integration test is tagged `manual` + `local` and listed
  in `//apps/telemetry:integration_tests`; nothing else is needed for CI to
  pick it up.
- The secrets `POSTGRES_USER`, `POSTGRES_PASSWORD`, `ELECTRIC_PWD`,
  `GRAFANA_PWD` and `ANALYSIS_PWD` must exist in this repo before the
  workflow tests anything; until then it only builds the images. The init
  scripts create databases, not roles, so `POSTGRES_USER` is `electric` and
  `ELECTRIC_PWD` equals `POSTGRES_PASSWORD`.
