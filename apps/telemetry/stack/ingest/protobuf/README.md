This folder contains protobuf schemas and generated Python modules used by the telemetry ingest service.

`can_packets_pb2.py` is Orion's schema compiled for Python. Its source is
`apps/BEVO/schema/can_packets.proto`, reached through the
`//apps/telemetry:current_schema_proto` alias. That proto is itself generated
from the CAN CSVs by `//apps/BEVO/schema:update_can_proto`; see the
[schema README](../../../../BEVO/schema/README.md). Never edit either file by
hand.

There are two ways to get the bindings. Both use `--config=local`, since the
default config needs a BuildBuddy API key.

## 1) Bazel build output (recommended for builds)

```bash
bazel build --config=local //apps/telemetry/stack/ingest:can_packets_pb2
```

Writes `bazel-bin/apps/telemetry/stack/ingest/protobuf/can_packets_pb2.py`.
It rebuilds whenever `can_packets.proto` changes.

## 2) Workspace writer (for local dev and non-Bazel runs)

```bash
bazel run --config=local //apps/telemetry/stack/ingest:update_can_packets_pb2
```

Copies the build output over `can_packets_pb2.py` in this folder. The
checked-in copy is only as fresh as the last time someone ran this, so run it
after `can_packets.proto` changes if anything imports
`stack.ingest.protobuf.can_packets_pb2` outside Bazel.

Bazel provides `protoc` through the `protobuf` module in `MODULE.bazel`, so
you don't install it yourself.
