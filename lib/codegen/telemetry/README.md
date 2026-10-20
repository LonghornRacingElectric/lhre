# Telemetry schema generation

Hermetic server-side artifacts derived from the validated CAN spec in
[`lib/spec`](../../spec/README.md). Firmware, the Pi gateway, and the telemetry
server therefore rebuild from the same edit instead of synchronizing CSV,
protobuf, JSON, and SQL copies by hand.

| Target | Output | Consumer |
| --- | --- | --- |
| `:can_packets_proto` | `can_packets.proto` | BEVO prost, ingest Python, bridge Go and viewer protobuf |
| `:can_json` | `can.json` | Build-time BEVO table generation and release auditing |
| `:orion_db_init` | `orion_db_init.sql` | Orion PostgreSQL initialization |
| `:schema_contract` | canonical JSON + schema ID | Registry/release compatibility metadata |
| `:schema_descriptor` | self-contained descriptor set | Build-time metadata validation |
| `:orion_models` | generated Python ORM classes | Analysis/ingest database mappings |
| `:orion_prisma` | generated Prisma schema | Optional viewer client generation |
| `:server_schema` | proto, CAN JSON, DDL, ORM, Prisma, contract, descriptor | CI and aggregate builds |

The generated protobuf preserves the deployed `lhre-2026` package, root
message, field numbers, scalar types, GPS fields, and board-status fields.
Compatibility-only type overrides live beside their telemetry binding in the
spec and appear in `wire.lock`; new fields use the derived type unless they
must match an existing deployed field.

Explicit `shared_source` bindings generate multiple CAN-to-field mappings but
only one protobuf field and SQL column. This preserves the inverter's legacy
RPM and torque-feedback sources without special cases in BEVO or ingest.

Build all outputs with:

```bash
bazel build //lib/codegen/telemetry:server_schema
```

Generated artifacts stay in `bazel-bin` and are never edited or checked in.

The [schema metadata](../../telemetry/README.md) describes the field definitions
used by both server and BEVO builds. IDs hash semantic metadata, not source
locations, documentation or protobuf serialization. Numeric tags and types
remain deployed-compatible; generated `reserved` declarations preserve the
textproto tombstones.

Metadata is not a runtime negotiation protocol. MQTT topics, protobuf payloads,
bridge RPCs and packet-ID handshakes are unchanged. Orion ORM classes and Go
protobuf bindings derive from this pipeline; the bridge enumerates fields and
seed groups from the generated descriptors while preserving its application
metrics. BEVO compiles typed CAN tables and logger columns from these outputs;
it does not read a mutable JSON file on the Pi.

Prisma telemetry models follow generated DDL. Group tables without declared
primary keys are marked `@@ignore`, rather than inventing uniqueness or a
migration; packet and common models with keys remain available. Set
`ORION_PRISMA_OUTPUT` to the desired client output directory when generating
the optional viewer client. Existing database volumes require operator-managed
migrations; initialization SQL and ORM generation do not migrate stored data.

Shared drive-day/event/track tables come from `common_schema.sql`, not CAN
telemetry fields. Historical Nightwatch/Angelique schemas are separate from
the current Orion textproto source and are not rewritten by this generator.
