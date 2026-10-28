# Telemetry schema metadata

Build-time metadata for the shared textproto schema. There is no runtime
registry service, MQTT negotiation, alternate payload envelope or sample
filtering in this package.

`//lib/codegen/telemetry:schema_contract` emits canonical semantic JSON and a
SHA-256 `schema_id`; `:schema_descriptor` emits the matching protobuf descriptor
set. The server's `//apps/telemetry:current_schema_bundle` and BEVO's
`//apps/BEVO:pi_core_manifest` use those same generated artifacts. Treat the
deployed server's manifest as the reference for future release selection.

`//lib/telemetry:contract` provides identity/descriptor validation and an
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
`release_compatible` permits additive unknown fields but rejects incompatible
shared definitions. Metadata alone does not guarantee safe handling of
arbitrary schema mismatches. The stdlib-only updater uses `Manifest`; the
build/server additionally validate serialized descriptors with `Schema`.
