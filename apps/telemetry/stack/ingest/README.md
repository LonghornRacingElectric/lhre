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

`docker-compose.yml` also defines Mosquitto, PostgreSQL, and Grafana. Their
local tags come from the root `_load` targets, so Compose must be run with
`--no-build` after `bazel run //apps/telemetry:load_core_images`.
