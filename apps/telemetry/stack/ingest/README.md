# Ingest

Forwards Artemis `TelemetryBatch` payloads from MQTT `artemis/data` through the
gRPC bridge to Kafka. Other telemetry topics are rejected. The bridge request
sets `car_type: Artemis` and `payload_format: artemis_updates_v1`.

The MQTT callback queues raw bytes without blocking on network I/O. A single
worker forwards them in order and retries failed or uncertain sends until Kafka
acknowledges the record. The queue holds 30,000 batches; a full queue logs and
drops new input. MQTT subscriptions are restored after reconnecting.

Ingest does not allocate packet IDs, write CSVs, open database sessions or apply
DDL. GPS travels in the native Artemis updates. There is no separate GPS JSON
input or packet-ID welcome exchange.

```bash
bazel build //apps/telemetry/stack/ingest:ingest_binary
bazel build //apps/telemetry/stack/ingest:ingest_image
bazel run //apps/telemetry/stack/ingest:ingest_load
bazel test //apps/telemetry/stack/ingest:ingest_smoke_test
```

`ingest_binary` uses `:mqtt_transport`, containing the MQTT/gRPC transport and
generated bridge bindings. `:ingest_lib` also exposes the shared decoding and
storage helpers used by consumers. These helpers are separate from the active
ingest executable.

The image packages the generated Artemis contract and descriptor under
`/schema/` for the [schema authority](../../schema_registry/README.md). The
bridge uses matching generated Artemis bindings. Network defaults come from
`net_configs.json`; `AWS_MQTT_IP` overrides the broker target.

The [Kafka bridge](../kafka/README.md) acknowledges successful raw publication.
The [database writer](../processors/db_writer/README.md) owns subsequent storage.
Database/Grafana provisioning is described in the [stack guide](../README.md).

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../README.md).
