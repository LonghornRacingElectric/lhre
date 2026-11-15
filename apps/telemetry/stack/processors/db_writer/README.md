# Database writer

Reads raw `sensor_data` protobuf using its own Kafka consumer group. Records are
grouped by car and inserted in one transaction per car: packet rows first, then
sensor tables through the existing bulk-insert helper, followed by session window
updates. Kafka offsets are committed after the database transactions succeed.
Failed inserts roll back and retry from Kafka after five seconds.

Packet IDs use `ON CONFLICT DO NOTHING`; sensor rows are written only for newly
inserted packets. This handles replay when the database commit succeeds before
the Kafka commit. Existing packets are first-writer-wins, including their sensor
rows. There are no additional database tables or application checkpoints.

```bash
bazel build //apps/telemetry/stack/processors/db_writer:db_writer_image
```

The core ingest Compose project starts this service; `build ingest` loads both
images. Defaults are 500 packets or 0.25 seconds per batch, configured through
`DB_BATCH_PACKETS` and `DB_BATCH_SECONDS`. `KAFKA_BOOTSTRAP_SERVERS`,
`KAFKA_INPUT_TOPIC`, and `KAFKA_GROUP_ID` default to `kafka:9092`, `sensor_data`,
and `telemetry-db-writer-v1`. Database credentials use existing ingest settings.

Deploy this service and MQTT ingest together, stopping the old protobuf database
writer first. A new group starts at the earliest retained Kafka record; existing
packets are skipped. Backlog recovery is limited by Kafka retention. Check that
retention covers expected database outages and monitor consumer lag.
