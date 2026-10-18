# Legacy Cargo schema fallback

These lhre-2026 CSV and protobuf files remain only for plain Cargo builds.
Bazel builds use the authoritative textproto spec under
[`lib/spec`](../../../lib/spec/README.md) and the generated artifacts under
[`lib/codegen/telemetry`](../../../lib/codegen/telemetry/README.md).

Do not edit this directory for a vehicle schema change. Edit `lib/spec`, run
`bazel run //tools/spec:lock`, and let Bazel regenerate the protobuf,
`can.json`, and Orion database DDL.

## Cargo fallback

- `can_packets.csv` and `can_bitfields.csv` feed the stdlib-only JSON generator
  used when `apps/BEVO/build.rs` runs outside Bazel.
- `can_packets.proto` supplies prost during that same fallback build.
- `bazel run //apps/BEVO/schema:update_can_proto` refreshes the fallback proto
  and its persistent `#N` CSV annotations when plain Cargo compatibility needs
  maintenance.

CI and deployed Bazel bundles do not consume these copies. Server-side
telemetry selects the generated artifacts through
`//apps/telemetry:current_schema_bundle`.
