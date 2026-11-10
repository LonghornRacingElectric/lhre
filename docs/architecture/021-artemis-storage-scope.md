# ADR-021: Scope frequency-pool storage to Artemis

- **Status:** Accepted; supersedes [ADR-020](020-frequency-pool-telemetry.md)
- **Date:** 2026-11

## Context

Orion must retain its existing packet-based database behavior. The new car is
Artemis; Angelique and Nightwatch no longer need runtime support. Both supported
cars can derive their initial field definitions from the current CAN spec.

## Decision

Apply the frequency-pool design from ADR-020 to Artemis only. Generate a distinct
Artemis protobuf package, database, ORM and Prisma client. Orion keeps its wire
identity, packet tables and existing Kafka persistence logic. One existing Kafka
database writer selects the appropriate storage path from the car header.

Remove retired-car decoders, database initialization and provisioning. Preserve
existing database volumes; this change neither deletes archived data nor migrates
Orion measurements into Artemis. New BEVO publishing remains a separate change.

## Alternatives considered

Converting Orion would change the existing-car path. Reusing a retired car name
for Artemis would mix identities and schema assumptions. A second database writer
would duplicate the Kafka ownership already present in the stack.

## Consequences

The server supports two explicit storage layouts. Artemis cross-subsystem queries
use session/time buckets; Orion continues to use packet relationships. Artemis
schema additions use the explicit additive apply command, with no background
schema service or compression policy required for the demo.
