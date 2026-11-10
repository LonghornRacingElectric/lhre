# Telemetry schema generation

Hermetic server-side artifacts derived from the validated CAN spec in
[`lib/spec`](../../spec/README.md). Firmware, the Pi gateway, and the telemetry
server therefore rebuild from the same edit instead of synchronizing CSV,
protobuf, JSON, and SQL copies by hand.

| Target | Output | Consumer |
| --- | --- | --- |
| `:can_packets_proto` | `can_packets.proto` | Legacy Orion ingest Python, bridge Go and viewer protobuf |
| `:can_json` | `can.json` | Build-time BEVO table generation and release auditing |
| `:orion_db_init` | `orion_db_init.sql` | Orion PostgreSQL initialization |
| `:schema_contract` | canonical JSON + schema ID | Registry/release compatibility metadata |
| `:schema_descriptor` | self-contained descriptor set | Build-time metadata validation |
| `:orion_models` | generated Python ORM classes | Analysis/ingest database mappings |
| `:orion_prisma` | generated Prisma schema | Optional viewer client generation |
| `:artemis_proto_source` | `artemis.proto` | Separate Artemis protobuf identity |
| `:artemis_db_init` | `artemis_db_init.sql` | Artemis Timescale initialization |
| `:artemis_models` / `:artemis_prisma` | ORM / Prisma | Artemis pools and read views |
| `:server_schema` | proto, CAN JSON, DDL, ORM, Prisma, wire metadata, signal catalog | CI and aggregate builds |
| `:signals_json` | `signals.json` | DISCOVER signal catalog |

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

`render_can_json()` produces the existing decoder artifact; `render_signals_json()`
produces `{car, schema_version, signals}` with the version only at the top level
and one row per protobuf snapshot field,
including gateway-generated fields. The CLI kinds are `can_json`, `signals_json`,
`proto`, and `sql`. The `json` kind remains an alias for existing BEVO fixture
builds; ORM, Prisma and storage kinds remain available for database generation.

Catalog names are qualified by snake-case group, e.g. `controls.motor_speed`.
Catalogs expose a single `rate_hz`: explicit `uplink_rate_hz` when present,
otherwise the CAN message's native `frequency_hz`. An inherited 0 means no trimming.
External signals use their uplink rate, or null when none is declared.
Shared sources with different effective rates produce a null rate.
No separate native/uplink rate fields are emitted. This does not change publishd yet.
Rows contain group, scalar/enum type, repeated flag, unit, module, rate,
and channel type. Signal rows do not repeat the schema version. Module is the lower-case CAN source
board or `gateway` for external signals. Shared producers and array slots are
deduplicated; conflicting module/rate values become null.
Unknown metadata is null, while a known dimensionless CAN unit is an empty string.
Catalog schema version is SHA-256 of canonical catalog content: car and signal
rows sorted by qualified name, keys sorted, compact UTF-8 JSON, integral floats
normalized to integers, and all `schema_version` fields excluded. Paths, whitespace,
and spec file ordering do not affect it; changing visible metadata or car does.
This catalog-content version is distinct from the input provenance hash in proto/SQL.
This artifact contains car identity but no firmware/device identity or CAN bit layout.

Build only discovery with `bazel build //lib/codegen/telemetry:signals_json`.
The viewer's [DISCOVER documentation](../../../apps/telemetry/analysis/database/viewer_tool/README.md)
describes serving current and archived catalogs.

Generated artifacts stay in `bazel-bin` and are never edited or checked in.

The [schema metadata](../../telemetry/README.md) describes the field definitions
used by both server and BEVO builds. IDs hash semantic metadata, not source
locations, documentation or protobuf serialization. Numeric tags and types
remain deployed-compatible; generated `reserved` declarations preserve the
textproto tombstones.

Metadata is not a runtime negotiation protocol. MQTT topics, protobuf payloads,
Artemis native transport is described below; legacy Orion transport remains separate. Orion ORM classes and Go
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

Every pool has a `(session_start_ms, time, sample_time_ns)` primary key; `time`
is BEVO Unix milliseconds and `sample_time_ns` is its original nanosecond receipt
time. Storage contract format 2 requires a fresh Artemis database. Floating columns and arrays use double precision. Protobuf
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
states and other scalar types use `last`. `last` skips SQL NULL and orders by
`sample_time_ns`, so unrelated partial rows cannot erase an observation. Missing samples remain NULL; there
is no implicit interpolation. Bucket boundaries are epoch-aligned.

Choose a bucket at least as wide as the slowest requested periodic pool. The
viewer does this from the generated contract; including cell arrays makes a
cross-group bucket at least one second. `min`/`max` retain peaks for conditional
analysis. Queries remain scoped to the recording session even when two
subsystems have the same timestamp. Raw pool rows retain every received
fresh observation and its actual receipt timestamp. BEVO forwards CAN updates
as received unless the spec explicitly sets a lower uplink rate.

Apply future additions with the [schema command](../../../apps/telemetry/scripts/README.md).
The [storage decision](../../../docs/architecture/021-artemis-storage-scope.md)
records the boundary between wire metadata and database layout.
## Publishing catalog history

`bazel run //tools/spec:publish_signals` builds the selected catalog and publishes
it under `apps/telemetry/catalogs/<car>/<schema_hash>.json`. Identical content is
a no-op. Existing versions are immutable; a new version is added before
`<car>/current.json` is replaced atomically. This explicit publishing step is
separate from hermetic Bazel generation; rebuilding or cleaning Bazel cannot
erase the archive. Configure `--catalog-root /absolute/path` or
`SIGNAL_CATALOG_DIR` for persistent deployment storage. Reactivate an older
version using `--source /absolute/path/to/<schema_hash>.json`.

The current spec declares Artemis. Orion outputs retain their legacy wire and
database identities for integration continuity. Other car folders can hold catalogs generated
from their own bundles, with the same JSON shape. Never choose the current schema
by hash order or filesystem timestamps; only the pointer selects it.

Uplink rates and channel types affect the discovery catalog only. They do not
move Artemis fields between pools or trigger schema application. Pool membership
continues to use native CAN frequency. Non-CAN fields remain in external pools.
The `ExternalSignal` metadata retains element layout at tag 7 and appends the
uplink limit at tag 9; these are spec metadata tags, not snapshot payload tags.

`:native_policy` generates `native_policy.json` from the same spec: numeric paths,
array layouts, pools, native rates, explicit uplink limits and channel classes.
BEVO compiles it with `:artemis_proto_source` and `:artemis_schema_contract`.
The Artemis root reserves tag 2/name `packet_id`; `FieldPath`, `TelemetryUpdate`
and `TelemetryBatch` carry explicit presence and receipt nanoseconds. The
`:artemis_schema_descriptor` target includes these messages for server authority
and release packaging. Generated outputs remain build artifacts.
