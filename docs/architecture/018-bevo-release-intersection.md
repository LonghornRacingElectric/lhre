# ADR-018: Reconcile each BEVO release against the deployed server

- **Status:** Accepted
- **Date:** 2026-11

## Context

Server images and BEVO executables deploy independently from textproto-based
builds. Whole-release rejection for one incompatible field prevents unrelated
measurements from reaching the server. The Pi must use existing GHCR artifacts
and its installed schema, without compiling or changing telemetry transport.

## Decision

The deployed server schema remains authoritative. Its read-only authority
continues exposing that schema and an ordered, digest-pinned list of approved
GHCR releases. Server schema definitions, SQL and ingest behavior are unchanged.

The BEVO updater compares the actual installed or selected producer manifest
with that server manifest by numeric path. Names, types, cardinality, units,
ordered array layout and shared enum-number meanings must agree. A same-tag
rename is excluded conservatively. Required roots and at least one measurement
must survive. Other available producer images do not restrict this intersection.

Bazel generates projection code alongside the producer bindings. Releases
advertise support in metadata and ship their schema and executable hashes.
The Pi either retains those executables or downloads a supported prebuilt
release. It activates a validated, release-bound plan and all three core
executables atomically, retaining current and previous complete activations.
Updating only the plan requires no registry download or build.

`publishd` projects at the pit uplink boundary using that plan. CAN acquisition,
IPC snapshots and local logging stay complete. There is no automatic unfiltered
fallback, new MQTT topic, protobuf envelope or per-frame release identity.
An explicit admin override creates an identity plan and bypasses server
selection under the operator's control.

## Alternatives considered

- A server-wide intersection across available images: lets unrelated releases
  constrain the server and complicates deployment without identifying senders.
- Build a matching executable on the Pi: violates the prebuilt artifact boundary.
- Rewrite ingest or introduce a negotiation protocol in telemetry: exceeds the
  release-selection scope; existing protobuf and packet-ID handling are retained.

## Consequences

Existing supported executables can continue sending their shared fields after
a divergent schema change. Operators must still run the updater and coordinate
restarts when the deployed server changes. Plans are loaded once at startup.
Old binaries without projection support need an available supported release.
Append-only tags and tombstones remain mandatory; projection is no permission
to recycle numbers.

Implicit proto3 scalar presence cannot distinguish an omitted value from a
valid default. Existing server JSON conversion may show excluded scalars as
zero/false. This decision guarantees wire-field exclusion, not database NULL
or per-frame availability tracking. Those semantics need a separate presence
design. See [protobuf presence](https://protobuf.dev/programming-guides/field_presence/).

See the [updater runbook](../../apps/BEVO/updater/README.md) and
[schema metadata](../../lib/telemetry/README.md). This extends
[ADR-017](017-schema-release-metadata.md)'s metadata boundary with BEVO-side
projection; its generated artifacts and transport choices remain in place.
