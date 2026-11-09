# Ingest protobuf bindings

Artemis and bridge bindings are Bazel outputs:

```bash
bazel build //apps/telemetry/stack/ingest:artemis_pb2 //apps/telemetry/stack/ingest:bridge_pb2
```

Artemis uses the textproto-derived `new_car_schema_proto` target. The transport
carries `TelemetryBatch` with explicit updated-field paths and observation
timestamps. Bridge bindings come from the shared RPC definition.

Import `stack.ingest.protobuf.artemis_pb2`, `bridge_pb2` and `bridge_pb2_grpc`.
Do not edit generated files or copy them into the source tree.
