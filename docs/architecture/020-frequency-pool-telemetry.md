# ADR-020: Store Orion telemetry in generated Timescale frequency pools

- **Status:** Superseded by [ADR-021](021-artemis-storage-scope.md)
- **Date:** 2026-11

## Context

The Orion schema grouped measurements by subsystem but required every row to
reference a full-snapshot packet ID. Signals used together have different CAN
rates. Cross-subsystem analysis needs explicit temporal alignment rather than
an implied packet-to-packet relationship. The existing Kafka database writer
and shared Bazel schema pipeline already provide the ingestion/build boundary.

## Decision

Generate Timescale hypertables from the existing subsystem and frequency
metadata. Each logical channel has one pool; shared producers use the fastest
declared rate. Gateway-only fields without declared rates remain explicitly
external. Pools are keyed by a time-derived recording session and BEVO Unix
milliseconds, without a packet relationship. Store floating measurements as
double precision without changing protobuf wire definitions.

Aggregate each participating pool into a common time bucket before combining
it with other pools. Preserve raw snapshot times and values; table membership
does not reconstruct acquisition times missing from the current producer.

Keep the existing Kafka writer. Generate physical models, storage metadata,
initialization DDL and read functions through Bazel. Apply additive changes
explicitly and transactionally; never rebuild a database volume to update its
schema. Storage identity is separate from the BEVO wire-contract identity.

## Alternatives considered

- One wide snapshot table retains unnecessary coupling between rates.
- A packet compatibility layer keeps the obsolete identity in current queries.
- A new schema-reconciliation service adds runtime coordination unnecessary for
  the demo; the explicit generated schema command is sufficient.

## Consequences

Current server reads move to session/time keys and bucketed alignment. Kafka
replays remain idempotent, and conflicting sample identities fail visibly.
Recording windows follow the existing five-minute gap rule and associate with
operator-created drive days through time. Historical packet-table migration,
independently sampled BEVO messages, automatic compression and continuous
aggregates remain separate work. The initial validation measures correctness,
not production throughput or compression ratios.
