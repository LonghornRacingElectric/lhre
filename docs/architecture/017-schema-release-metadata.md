# ADR-017: Keep schema release metadata separate from telemetry transport

- **Status:** Accepted
- **Date:** 2026-10

## Context

BEVO executables and server images build from the same textproto source but
deploy independently. A separate registry/updater needs immutable schema and
executable identities to select a release matching the deployed server.
This does not require changing the production telemetry protocol.

## Decision

Bazel generates a canonical semantic schema manifest and protobuf descriptor
from `lib/spec`. The server schema bundle exposes both. BEVO release metadata
pins that same schema identity, its runtime CAN JSON and exactly the core
`cand`, `loggerd` and `publishd` executables by digest.

Definition-level comparison checks numeric paths, names, types, cardinality,
units, enum meanings and array layouts. Numeric tags/names are append-only;
deletions become protobuf reservations. The deployed server manifest is the
reference for future release selection.

Metadata generation does not add MQTT topics, envelopes, filtering, RPCs,
migrations or changes to existing daemons/ingest. A separate updater handles
release compatibility against a read-only server authority. Digest-pinned OCI
releases carry separate metadata and executable layers; the Pi downloads
prebuilt files without Docker, Bazel, Cargo or persistent build/image caches.
Verified releases activate atomically, retaining current and previous versions.

Admin overrides can pin a local BEVO release or manually replace server images
and their authoritative schema artifacts. They bypass automatic selection,
not protobuf compatibility rules. Publishing and service restarts are explicit.

## Alternatives considered

- Infer identity from `.proto` text: paths/comments change without changing
  field definitions.
- Add runtime negotiation while introducing metadata: expands the production
  protocol and deployment scope unnecessarily.
- Run builds on the Pi: consumes storage; deploy already-built executables.

## Consequences

Releases carry comparable schema metadata without changing data flow. This
does not make arbitrary mixed-schema traffic safe or dynamically replace a
Rust executable's compiled types. Operators still deploy matching or reviewed
wire-compatible releases and manage database migrations.

Schema adoption is build-time plumbing: current Orion Python/Go bindings,
ORM/DDL and optional viewer Prisma definitions derive from the shared source.
BEVO compiles generated CAN tables and CSV columns instead of reading a mutable
JSON mapping. Historical-car schemas and application-derived metrics remain
separate. These hooks do not replace telemetry transport logic.

See [schema metadata](../../lib/telemetry/README.md) and
[BEVO](../../apps/BEVO/README.md) for targets and artifact paths.
