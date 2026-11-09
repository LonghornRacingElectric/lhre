# Ingest protobuf bindings

Orion, Artemis and bridge bindings are Bazel outputs only:

```bash
bazel build //apps/telemetry/stack/ingest:can_packets_pb2 //apps/telemetry/stack/ingest:artemis_pb2 //apps/telemetry/stack/ingest:bridge_pb2
```

Orion uses `current_schema_proto`; Artemis uses `new_car_schema_proto`.
Both derive from the current textproto fields with distinct protobuf packages
and root messages. Orion's deployed wire identity is unchanged. The Artemis
producer is implemented separately; this branch supplies server bindings.

Import paths are `stack.ingest.protobuf.can_packets_pb2`,
`stack.ingest.protobuf.artemis_pb2` and `stack.ingest.protobuf.bridge_pb2`.
There are no checked-in fallback copies. Angelique/Nightwatch bindings are
removed rather than shipped in the ingest image.
