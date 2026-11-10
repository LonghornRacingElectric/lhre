# ADR-024: Query Artemis pools through the current gateway

- **Status:** Accepted; clarifies the current-car scope of [ADR-023](023-per-car-signal-catalogs.md)
- **Date:** 2026-11

## Context

Artemis is the current car in this monorepo. Orion remains a legacy integration
reference. The imported query adapter assumed packet joins, while Artemis uses
frequency pools keyed by session and timestamp.

## Decision

Declare Artemis as the current catalog identity. The query gateway reads only
Artemis, maps catalog signals through the generated storage contract, and pages
raw observations by timestamp, recording session and signal name. It uses no
packet identifier or cross-pool raw join. Existing bucket interfaces remain the
alignment path for cross-subsystem analysis.

## Alternatives considered

Routing the new gateway only to Orion misses its intended car. Treating timestamps
as packet IDs or installing compatibility packet views preserves an unnecessary
relationship. Migrating Orion into Artemis is outside this change.

## Consequences

Orion's existing wire/database targets remain available for legacy integration.
Published catalog archives remain independent. Current Artemis discovery and
queries work together without database reconciliation. Uplink limits remain
metadata policy and do not change native-frequency storage placement.
