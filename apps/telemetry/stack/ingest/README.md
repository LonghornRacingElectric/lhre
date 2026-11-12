# Ingest

Ingest forwards MQTT protobuf frames through the gRPC bridge to Kafka.
The core [database writer](../processors/db_writer/README.md) consumes
`sensor_data`, decodes the selected protobuf schema, and persists complete
batches to PostgreSQL. Protobuf frames never enter MQTT's database queues.

The MQTT callback only queues raw forwarding and optional Orion CSV work;
CSV decoding and file writes run on their own worker. GPS JSON (`orion/gps`),
legacy table JSON/pickle topics, and legacy base64 inputs retain their existing
database paths. Their queues and the pre-Kafka forwarding queue remain bounded:
this change provides recovery for records acknowledged by Kafka, not lossless
MQTT delivery during an unlimited broker outage.

The forwarding worker retries failed/uncertain sends in order until Kafka
acknowledges the raw record. A full forwarding queue still logs and drops new
input; car logs remain the recovery source for that pre-Kafka window.

BEVO welcome lookups run on a control worker. They wait for queued bridge work,
then choose a packet ID above both Postgres and a snapshot of retained raw Kafka
records, so database lag cannot reuse IDs after a producer restart. This scans
retained history and may delay startup; it fails closed when either lookup fails.
The existing protocol assumes one active publisher per car; concurrent producer
allocation is not supported. A Kafka scan has a 120-second processing deadline
in addition to client network timeouts. Keep the raw retention window large
enough to cover database outages. See the writer guide for cutover steps.

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
