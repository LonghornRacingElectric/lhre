# Ingest protobuf bindings

Current Orion bindings are Bazel outputs only:

```bash
bazel build //apps/telemetry/stack/ingest:can_packets_pb2
bazel build //apps/telemetry/stack/ingest:bridge_pb2
```

The first compiles `//apps/telemetry:current_schema_proto`, generated from the
authoritative textproto. The second compiles the existing Kafka bridge RPC
schema, shared with Go. Outputs retain the import paths
`stack.ingest.protobuf.can_packets_pb2` and `stack.ingest.protobuf.bridge_pb2`.

There are no checked-in Orion or bridge Python fallback copies, and no target
that writes generated bindings into the workspace. The historical Nightwatch
and Angelique inputs/bindings remain separate; they are not the current car's
textproto schema.
