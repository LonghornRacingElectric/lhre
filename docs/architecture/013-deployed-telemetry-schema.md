# ADR-013: Generate server artifacts while preserving the deployed telemetry schema

- **Status:** Accepted
- **Date:** 2026-10

## Context

ADR-008 made `lib/spec` the CAN source of truth, but its first phase generated
only firmware C++. BEVO and the telemetry server still consumed the migrated
CSV, protobuf, and SQL copies, so a valid CAN-spec change could build firmware
without rebuilding the matching server artifacts. Pure type derivation also
changed several existing lhre-2026 protobuf fields from `float` to integer or
`double`, which is wire-incompatible despite retaining the field number.

GPS and board-heartbeat fields add another constraint: they are part of the
deployed snapshot schema but are produced by the gateway rather than a CAN
frame, so they cannot be recovered from message bindings alone.

## Decision

`//lib/codegen/telemetry` hermetically generates the deployed snapshot proto,
the BEVO runtime CAN JSON, and the Orion database initialization DDL from
`//lib/spec`. BEVO, telemetry images, aggregate builds, and telemetry CI consume
those targets through Bazel instead of the legacy CSV/proto copies.

`groups.textproto` owns root-message field numbers and gateway-produced fields.
Existing lhre-2026 fields retain their exact protobuf scalar types through
reviewable `wire_type` compatibility overrides; new CAN-backed fields derive
their type by default. `wire.lock` records both kinds.

This supersedes ADR-008 only where it required every snapshot type to be purely
derived. Its single-spec, ledger, validation, and generated-artifact decisions
remain in force.

## Alternatives considered

- **Keep updating server copies manually.** Rejected because firmware and
  telemetry can pass independent CI runs with incompatible schemas.
- **Accept the newly derived protobuf types.** Rejected because changing a
  protobuf wire type under an existing field number breaks deployed consumers
  and archived payloads.
- **Drop GPS because it is absent from CAN CSV rows.** Rejected because the
  gateway fills those live fields from NMEA and server queries depend on them.

## Consequences

A textproto edit invalidates firmware codegen, gateway decode configuration,
the snapshot proto, Python/prost bindings, and Orion DDL in one Bazel graph.
Compatibility overrides are intentional migration debt, but they are visible
in the source and lockfile instead of being hidden in a second schema.
