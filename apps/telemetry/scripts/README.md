# Schema helpers

Orion and Artemis definitions come from [`lib/spec`](../../../lib/spec/README.md)
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
schema authority. The old source-patching CLI is removed. Angelique and Nightwatch are retired. Database migrations remain operator-managed.

## Apply Artemis storage changes

Builds generate artifacts; they do not rebuild or mutate an existing database.
The normal stack initialization creates a fresh Artemis database from the full
generated SQL. For later schema additions, inspect and apply explicitly:

```bash
bazel run --config=local //apps/telemetry:schema_apply
bazel run --config=local //apps/telemetry:schema_apply -- --apply
```

The command uses the existing Artemis connection settings. `ARTEMIS_DATABASE_URL`
can explicitly override them (a SQLAlchemy PostgreSQL URL). Use the database
owner for DDL. The command takes an advisory lock shared with the writer,
checks existing types/keys and prior field-path mappings, adds nullable
columns/new pools, refreshes generated read interfaces, and records the storage
contract in the same transaction. Failed DDL rolls back. No table or measurement
column is dropped. Existing columns for removed fields remain stored.

Renames, retypes and moving an existing field between pools require a reviewed
migration; this command refuses them. It also refuses the old packet-table
layout: historical migration is outside this change. Never reset a volume to
apply a sensor addition. Do not use Prisma `db push` on the telemetry database.

Deploy order: build schema and server images, apply additions, then restart the
existing Kafka database writer and viewer. The writer checks its generated
storage hash before writing and leaves Kafka offsets uncommitted if the schema
has not been applied. Reapplying a matching schema preserves all measurements.
