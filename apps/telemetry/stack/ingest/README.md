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
Compose requires `ORION_DB_INIT_SQL` to point at the generated SQL; there is
no checked-in fallback. The server devtool rebuilds that target before mounting
it. Initialization does not migrate an existing database volume.
