# Telemetry schema generation

Hermetic server-side artifacts derived from the validated CAN spec in
[`lib/spec`](../../spec/README.md). Firmware, the Pi gateway, and the telemetry
server therefore rebuild from the same edit instead of synchronizing CSV,
protobuf, JSON, and SQL copies by hand.

| Target | Output | Consumer |
| --- | --- | --- |
| `:can_packets_proto` | `can_packets.proto` | BEVO prost and telemetry Python bindings |
| `:can_json` | `can.json` | BEVO runtime CAN decoder |
| `:orion_db_init` | `orion_db_init.sql` | Orion PostgreSQL initialization |
| `:signals_json` | `signals.json` | DISCOVER signal catalog |
| `:server_schema` | all four | CI and aggregate builds |

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
produces `{schema_version, signals}` with one row per protobuf snapshot field,
including gateway-generated fields. The CLI kinds are `can_json`, `signals_json`,
`proto`, and `sql` (the former `json` kind is now `can_json`).

Catalog names are qualified by snake-case group, e.g. `controls.motor_speed`.
Catalogs expose a single `rate_hz`: explicit `uplink_rate_hz` when present,
otherwise the CAN message's native `frequency_hz`. An inherited 0 means no trimming.
External signals use their uplink rate, or null when none is declared.
Shared sources with different effective rates produce a null rate.
No separate native/uplink rate fields are emitted. This does not change publishd yet.
Rows contain group, scalar/enum type, repeated flag, unit, module, rate,
channel type, and schema version. Module is the lower-case CAN source
board or `gateway` for external signals. Shared producers and array slots are
deduplicated; conflicting module/rate values become null.
Unknown metadata is null, while a known dimensionless CAN unit is an empty string.
Schema version is the same spec provenance hash written to generated proto/SQL.
This artifact contains no deployment identity or CAN bit layout.

Build only discovery with `bazel build //lib/codegen/telemetry:signals_json`.
The viewer's [DISCOVER documentation](../../../apps/telemetry/analysis/database/viewer_tool/README.md)
describes serving current and archived catalogs.

Generated artifacts stay in `bazel-bin` and are never edited or checked in.
