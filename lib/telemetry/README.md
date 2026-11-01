# Telemetry schema metadata

Schema metadata and definition comparison for the shared textproto source.
The deployed server remains authoritative; the separate BEVO updater uses
this package to compute a plan against its actual executable release.

`//lib/codegen/telemetry:schema_contract` emits canonical semantic JSON and a
SHA-256 `schema_id`; `:schema_descriptor` emits the matching protobuf descriptor
set. The server's `//apps/telemetry:current_schema_bundle` and BEVO's
`//apps/BEVO:pi_core_manifest` use those same generated artifacts. Treat the
deployed server's manifest as the reference for future release selection.

`//lib/telemetry:contract` provides identity/descriptor validation and a
`compare(producer, server)` helper. It reports compatible numeric
paths/shared enum values and incompatible definitions by path. Names, types,
cardinality, units and ordered array layouts must match. Additive fields and
enum values retain the existing field intersection. The helper does not
decode, rewrite, filter or transmit telemetry.

Schema IDs ignore source paths, comments and declaration order. Preserve
protobuf tags/names and tombstone deletions in the textproto source; field
declaration order is not wire identity. See the
[protobuf evolution guide](https://protobuf.dev/programming-guides/proto3/#updating)
and [protobuf best practices](https://protobuf.dev/best-practices/dos-donts/).

Run `bazel test //lib/telemetry:contract_test` for definition-level checks.
Existing Paho integration tests remain responsible for the server data path.
The [separate Pi updater](../../apps/BEVO/updater/README.md) uses these
definitions to select releases against the
[deployed server authority](../../apps/telemetry/schema_registry/README.md).
`release_compatible` retains its strict whole-release check for additive
evolution. `release_plan(metadata, producer, server)` instead records accepted
numeric paths, shared enum numbers and reasons for excluded producer fields.
It requires a projection-capable executable, unchanged time/packet_id and at
least one shared measurement. Group/leaf renames, incompatible types, units
or ordered array layouts exclude affected fields without discarding unrelated
measurements. It never mutates the server manifest or combines other releases.
The plan includes both schema identities and exact executable metadata;
its digest identifies an immutable release/plan activation on the Pi.

The stdlib-only updater uses `Manifest`; the build/server additionally validate
serialized descriptors with `Schema`. Projection changes only the BEVO uplink.
Implicit proto3 scalar presence still maps omitted fields to defaults in
existing consumers; wire exclusion does not establish database NULLs or
per-frame availability. MQTT topics, message definitions and server ingestion
are unchanged. See [field presence](https://protobuf.dev/programming-guides/field_presence/).
