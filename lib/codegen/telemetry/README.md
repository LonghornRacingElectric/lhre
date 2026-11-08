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
produces `{car, schema_version, signals}` with the version only at the top level
and one row per protobuf snapshot field,
including gateway-generated fields. The CLI kinds are `can_json`, `signals_json`,
`proto`, and `sql` (the former `json` kind is now `can_json`).

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

## Publishing catalog history

`bazel run //tools/spec:publish_signals` builds the selected catalog and publishes
it under `apps/telemetry/catalogs/<car>/<schema_hash>.json`. Identical content is
a no-op. Existing versions are immutable; a new version is added before
`<car>/current.json` is replaced atomically. This explicit publishing step is
separate from hermetic Bazel generation; rebuilding or cleaning Bazel cannot
erase the archive. Configure `--catalog-root /absolute/path` or
`SIGNAL_CATALOG_DIR` for persistent deployment storage. Reactivate an older
version using `--source /absolute/path/to/<schema_hash>.json`.

The current spec declares Orion. Other car folders can hold catalogs generated
from their own bundles, with the same JSON shape. Never choose the current schema
by hash order or filesystem timestamps; only the pointer selects it.
