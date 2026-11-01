# Schema helpers

Current Orion definitions come from [`lib/spec`](../../../lib/spec/README.md)
through [`lib/codegen/telemetry`](../../../lib/codegen/telemetry/README.md),
not from editable `.proto`, SQL, ORM or Prisma copies in the source tree.

```bash
bazel build //lib/codegen/telemetry:server_schema
```

The old `sync_schema.sh` entry point now forwards to that Bazel target and
does not patch source files. Its former `gen_orion` snapshots and Kafka
protobuf-rewriting script were removed. The pure SQL parsing/type helpers
in `generate_schema.py` are a Bazel dependency for generating Prisma's common
non-telemetry models from `common_schema.sql`; they are not a second Orion
schema authority. The old source-patching CLI is removed. Historical car
inputs remain unchanged. Database migrations remain operator-managed.
