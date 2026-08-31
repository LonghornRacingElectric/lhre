# ADR-011: Bazel owns every telemetry stack image; Compose only runs loaded tags

- **Status:** Accepted
- **Date:** 2026-08

## Context

[ADR-010](010-telemetry-stack-in-bazel.md) put part of the imported telemetry
tree into Bazel but left the Go Kafka bridge and most processors in Dockerfiles.
Its image aggregate covered four services, omitted required Python runfiles,
and was Linux-compatible only when the entire command selected a Linux target
platform. CI built those OCI layouts and then used `docker compose --build`, so
the integration test exercised different images.

The deploy workflow needs one reproducible core build, independent rebuilds for
each service, and a clear answer to whether an operation creates an artifact or
changes the local Docker daemon. Developers commonly issue the build from
Apple Silicon, while deployment currently runs Linux/AMD64.

The current schema is also addressed by its BEVO generator location. A future
version registry must be able to change schema selection without rewiring every
server target, but registry semantics are not yet decided.

## Decision

- Bazel builds every runnable core and optional processor image. Repo-owned
  services expose `<service>_binary`, `_image`, `_load`, `_push`, and
  `_smoke_test`; pinned upstream infrastructure exposes `_image`, `_load`, and
  `_smoke_test`.
- `_image` produces an OCI layout in `bazel-bin`. `_load` is the explicit
  Docker-daemon side effect. Compose contains only image tags and is started
  with `--no-build` for Bazel-managed components.
- `core_images`, `optional_images`, and `all_images` are build aggregates.
  Matching executable load aggregates and `core_up` provide the local runtime
  workflow. Compatibility aliases preserve the imported labels for one
  migration cycle.
- A transition packages each repo-owned binary for `//platforms:linux_amd64`
  without changing the caller's host configuration. Python images include the
  binary's full runfiles closure; the Go bridge builds with rules_go. Runtime
  bases, upstream images, language toolchains, and dependency locks are pinned.
- `//apps/telemetry:current_schema_bundle` is the service-facing selection
  boundary. It selects the current BEVO artifacts today. No schema registry,
  negotiation, or multiple-version decode behavior is introduced by this
  decision.

## Alternatives considered

- **Keep Dockerfiles as the deployment build and use Bazel only for tests.**
  This preserves two dependency graphs and lets CI validate an image other than
  the one Bazel reports as built.
- **Have `_load` also start Compose.** Loading and orchestration then become
  inseparable, making a per-image rebuild unexpectedly restart services. The
  aggregate `core_up` is the explicit combined operation instead.
- **Write Docker archives into the source tree.** OCI layouts already live in
  Bazel's output tree and `oci_load` can stream them directly. Source artifacts
  add cleanup and stale-image ambiguity.
- **Implement schema versioning during the image port.** The wire identity and
  compatibility policy are unresolved. A stable dependency seam enables that
  later work without pretending the protocol exists today.

## Consequences

- A macOS developer and Linux CI build the same Linux/AMD64 container closure,
  while direct binary targets remain useful on the host.
- `bazel build` never makes an image visible to Docker. Operators must run a
  load target before Compose, or use `core_up`/the server devtool.
- Docker-backed smoke tests validate each loaded tag and expected executable;
  they remain manual/local because Docker is intentionally outside the
  hermetic test sandbox.
- Supporting ARM64 images later requires a second explicit container platform
  and image index. It is not implied by this decision.
- A schema-changing board branch still needs a matching server branch until a
  future protocol carries schema identity and the server can resolve compatible
  versions.
