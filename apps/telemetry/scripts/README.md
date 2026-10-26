# Schema helpers

Current Orion protobuf, CAN decode JSON and initialization SQL come from
[`lib/spec`](../../../lib/spec/README.md) through
[`lib/codegen/telemetry`](../../../lib/codegen/telemetry/README.md).

```bash
bazel build //lib/codegen/telemetry:server_schema
```

`sync_schema.sh` forwards to that Bazel target. It no longer patches source
files or recreates a checked-in Orion initialization SQL file. The older SDD
design and usage pages describe the historical source-patching workflow, not
the supported Orion build. Database migrations remain operator-managed.
