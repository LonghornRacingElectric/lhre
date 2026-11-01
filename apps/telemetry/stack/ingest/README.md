# Ingest

Ingest accepts MQTT telemetry, decodes the selected protobuf schema, persists
packets to PostgreSQL, and forwards the raw stream to Kafka.

```bash
bazel build //apps/telemetry/stack/ingest:ingest_binary
bazel build //apps/telemetry/stack/ingest:ingest_image
bazel run //apps/telemetry/stack/ingest:ingest_load
bazel test //apps/telemetry/stack/ingest:ingest_smoke_test
```

The image includes its complete Python runfiles plus the car configuration and
network configuration opened by path at runtime. The generated
`can_packets_pb2.py` is built from `//apps/telemetry:current_schema_proto`;
never edit or check in the Bazel output. The legacy `ingest_tarball` label is an
alias of `ingest_load` for one migration cycle.

`/schema/schema_contract.json` and `/schema/schema_descriptor.pb` are packaged
from those same inputs for the [deployed authority](../../schema_registry/README.md).
They describe this image's compiled schema; they are not runtime overrides
or definitions loaded by the MQTT handler. Rebuild the bridge alongside
ingest when deploying a schema change.

Orion database models also come from `//lib/codegen/telemetry:orion_models`;
analysis re-exports their existing class names. The CSV header's group list is
derived from those models. No MQTT routing, packet-ID handshake or ingest
queue logic changes are needed to adopt a schema edit.

The checked-in Orion DDL and Python bindings were removed. Both Python and Go
bridge bindings are generated from the one bridge RPC definition by Bazel;
the RPC contract itself is unchanged. The old workspace-copy target and
standalone Dockerfile are no longer supported build paths.

`docker-compose.yml` also defines Mosquitto, PostgreSQL, and Grafana. Their
local tags come from the root `_load` targets, so Compose must be run with
`--no-build` after `bazel run //apps/telemetry:load_core_images`.

The devtool and integration runner prepare this path automatically. For direct
Compose calls, including `down` and `logs`, export the selected absolute path
first; those non-creating commands do not need to build the file. Container
creation must still use current Bazel-generated SQL or an explicit admin override.

Build `//apps/telemetry:current_schema_sql` and set `ORION_DB_INIT_SQL` to the
absolute `bazel-bin/lib/codegen/telemetry/orion_db_init.sql` output before a
direct Compose invocation. Without this variable Compose fails, and a missing
host file is not created as a directory. There is no checked-in Orion SQL
fallback. The [devtool](../README.md) checks the Bazel target and forwards the
path even when Docker requires sudo. Initialization applies only to a new
database volume; migrate deployed databases explicitly.
