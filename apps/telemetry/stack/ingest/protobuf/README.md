This folder contains protobuf schemas and generated Python modules used by the telemetry ingest service.

There are two common workflows for the generated CAN schema bindings:

## 1) Bazel build output (recommended for builds)

Compiles the Longhorn-generated CAN schema into a Python `_pb2.py` module as a normal Bazel output:

- `bazel build //apps/telemetry/stack/ingest:can_packets_pb2`

Output path:

- `bazel-bin/apps/telemetry/stack/ingest/protobuf/can_packets_pb2.py`

This target depends on `//apps/telemetry:current_schema_proto`, which selects
the protobuf generated from `//lib/spec`. A textproto change therefore
regenerates both this Python module and the matching Orion database DDL.

## 2) Workspace writer (useful for local dev / non-Bazel runs)

Materializes the generated module into this source folder:

- `bazel run //apps/telemetry/stack/ingest:update_can_packets_pb2`

Output path:

- `apps/telemetry/stack/ingest/protobuf/can_packets_pb2.py`

Note: Bazel provides the `protoc` compiler via the `protobuf` module dependency declared in `MODULE.bazel`.
