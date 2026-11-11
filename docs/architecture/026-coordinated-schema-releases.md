# ADR-026: Coordinate schema releases without replacing running authority

- **Status:** Accepted
- **Date:** 2026-11

## Context

Publishing code does not identify the running server. Server storage and BEVO
executables must evolve together while preserving recordings and offline
operation. Protobuf compatibility alone does not identify compiled CAN layouts.

## Decision

Explicit manual publication, gated by successful CI verification, publishes
BEVO executables and a matching generated server contract bundle. Only a manual
run on the configured deployment branch promotes the discovery channel. PR and
push checks do not publish. The server caches verified metadata from
digest-pinned releases. Discovery does not deploy server code or change the
authoritative schema. Each producer's intersection is computed independently.

Operator deployment prepares the selected bundle against local generated
artifacts and existing deployed semantics. Only new nullable columns and new
frequency pools apply automatically, using the existing transactional migration
ledger and writer advisory lock. Renames, retirements affecting read interfaces,
retypes, semantic changes and pool moves require explicit migrations. Artemis
keeps one database and its historical measurements.

The automatic authority checks running core and enabled schema consumers,
their CAN/storage identities, and the applied storage contract. Mixed or
unprepared deployments refuse synchronization. This extends ADR-019's running
consumer authority and ADR-018's per-release projection.

BEVO bootstrap powers the modem/GPIO hardware and configures CAN before updater
synchronization. Startup prefers a matching prebuilt release and activates all
core executables and their plan together. Failed synchronization can continue a
verified installed activation. Periodic checks never replace live daemons.

## Consequences

Additive changes preserve historical values without a second database writer
or runtime protobuf loader. Extra columns remain during executable rollback.
Schema files, code revisions, CAN identities and deployment state stay distinct.
Board identity still relies on pushed-before-flash policy; passive decoding
cannot detect every semantic mismatch. A board schema heartbeat is deferred.
