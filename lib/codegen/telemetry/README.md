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
| `:artemis_proto_source` | `artemis.proto` | Separate Artemis protobuf identity |
| `:artemis_db_init` | `artemis_db_init.sql` | Artemis Timescale initialization |
| `:artemis_models` / `:artemis_prisma` | ORM / Prisma | Artemis pools and read views |
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

Orion ORM and Prisma models retain packet-based storage. Artemis models follow
the pool DDL and session/time read views. Set `<CAR>_PRISMA_OUTPUT` when generating
a client; model generation does not apply database DDL.

Shared drive-day/event/track tables come from `common_schema.sql`, not CAN
telemetry fields. Only Orion and Artemis are supported; Nightwatch/Angelique runtime schemas are
retired. Both current car schemas derive from the current textproto fields.

## Regression checks

```bash
bazel test --config=local //lib/codegen/telemetry:generated_contract_test //apps/BEVO/schema_tests:schema_propagation_test
```

The contract test compares the emitted descriptor with the reviewed wire lock,
including tombstones, and checks generated DDL/ORM column names and types.
It follows the lock rather than pinning packet counts. This is an offline
consistency check, not a comparison with a production database or a migration
test; database constraints and deployed drift still need operator review.

The [propagation regression](../../../apps/BEVO/schema_tests/README.md) builds
baseline and appended fixture schemas through the production generators and
BEVO Rust macro. It checks database artifacts and decodes a compiled BEVO
packet using the generated server descriptor. Both targets are part of normal
`bazel test //...`; no GitHub, registry, image pull or service is needed.
Test fixtures and generated outputs never replace the deployed schema.

## Frequency-pool storage

Artemis uses TimescaleDB. Orion keeps its existing packet/FK storage. The generator assigns each measurement to its existing
subsystem and exact CAN frequency: `dynamics_333hz` for motion,
`dynamics_100hz` for suspension, `controls_333hz` for pedals/torque,
`thermal_10hz` for temperatures, `pack_1hz` for cells, and the other declared
rates. Shared-source fields have one home at their fastest producer rate.
Gateway fields without a declared rate use an `external` pool; no frequency
is invented for GPS or board heartbeats.

Every pool has a `(session_start_ms, time)` primary key; `time` is BEVO Unix
milliseconds. Floating columns and arrays use double precision. Protobuf
numbers, types, units and array ordering are unchanged. There is no Artemis
packet table, packet foreign key, or packet compatibility view.

Additional outputs are `:storage_contract` (`storage_contract.json`) and
`:storage_sql` (`storage_update.sql`). They are included in `:server_schema`.
The storage hash includes field paths, pool membership, SQL types and bucket
rules; it is separate from the wire schema ID used by the BEVO updater.
Generated ORM/Prisma models describe the physical pools and read views.

The subsystem views expose 100 ms buckets keyed by session and time. For
cross-rate analysis, call the generated `<subsystem>_buckets` function with a
common integer bucket width and `[from_ms, to_ms)` bounds. Each pool aggregates
before the join, so independently sampled rows cannot multiply one another.
Floating scalars support `avg` (default), `min`, `max`, and `last`; arrays,
states and other scalar types use `last`. Missing samples remain NULL; there
is no implicit interpolation. Bucket boundaries are epoch-aligned.

Choose a bucket at least as wide as the slowest requested periodic pool. The
viewer does this from the generated contract; including cell arrays makes a
cross-group bucket at least one second. `min`/`max` retain peaks for conditional
analysis. Queries remain scoped to the recording session even when two
subsystems have the same timestamp. Raw pool rows retain every received
snapshot and its actual timestamp: the 50 Hz uplink does not become 333 Hz
merely because a source is assigned to that pool.

Apply future additions with the [schema command](../../../apps/telemetry/scripts/README.md).
The [storage decision](../../../docs/architecture/021-artemis-storage-scope.md)
records the boundary between wire metadata and database layout.
