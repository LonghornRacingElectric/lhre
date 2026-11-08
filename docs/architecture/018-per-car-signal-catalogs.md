# ADR-018: Publish immutable signal catalogs per car

- **Status:** Accepted
- **Date:** 2026-11

## Context

DISCOVER originally read one replaceable Bazel output and assumed Orion.
Different cars and historical versions need independent identities and durable
storage. Build output paths and timestamps cannot identify the schema used by
a client, and cleaning Bazel must not delete catalog history.

## Decision

Each spec input bundle declares one car slug; the current bundle is Artemis.
The catalog contains car identity and a SHA-256 version of canonical catalog
content, excluding version fields. This identifies the visible signal catalog,
not a firmware decoder or the full CAN wire contract.

Bazel only generates deterministic artifacts. An explicit publisher stores
immutable `<car>/<hash>.json` versions outside Bazel outputs and atomically
selects `<car>/current.json`. Publishing unchanged content changes no files.
Default API requests follow that pointer; exact-version requests never consult
another car or substitute a different version. Publishing an older catalog
explicitly supports rollback. Archive JSON is runtime data and is not committed.

## Alternatives considered

- Archive under `bazel-bin`: builds and cleanups can discard historical files.
- Choose latest by timestamp or hash order: neither encodes deployment intent.
- Reuse the input provenance hash: source paths and invisible source edits can
  change it without changing the catalog clients receive.

## Consequences

The archive root must be persisted and backed up. Future cars supply their own
spec bundles and use the same publisher/API format; empty car folders do not
pretend to contain a schema. Existing Orion-named protobuf and SQL outputs stay
compatible. This decision does not implement runtime decoding across firmware
versions. See the [archive guide](../../apps/telemetry/catalogs/README.md).
