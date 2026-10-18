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
| `:server_schema` | all three | CI and aggregate builds |

The generated protobuf preserves the deployed `lhre-2026` package, root
message, field numbers, scalar types, GPS fields, and board-status fields.
Compatibility-only type overrides live beside their telemetry binding in the
spec and appear in `wire.lock`; new fields use the derived type unless they
must match an existing deployed field.

Build all outputs with:

```bash
bazel build //lib/codegen/telemetry:server_schema
```

Generated artifacts stay in `bazel-bin` and are never edited or checked in.
