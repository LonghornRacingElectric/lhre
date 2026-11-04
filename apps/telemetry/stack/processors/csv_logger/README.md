# CSV logger

Reads raw `sensor_data` using its own Kafka consumer group and writes Orion CSVs
under `$LOG_BASE_DIR/orion`. Headers follow the generated protobuf schema in
field order, using `packet.<field>` for top-level scalars and `<group>.<field>`
for sensor fields. Arrays are JSON cells; absent groups leave blank cells.

The ingest Compose project starts this service and mounts the existing
`apps/telemetry/logs` directory. `server_devtool.sh build ingest` loads both
images. MQTT no longer creates CSV files or queues CSV writes.

```bash
bazel build //apps/telemetry/stack/processors/csv_logger:csv_logger_image
```

A timestamped CSV is opened on each logger start. Rows are flushed before Kafka
offsets are committed; a crash can replay rows. The consumer group defaults to
`telemetry-csv-logger-v1`, brokers to `kafka:9092`, topic to `sensor_data`, and
`LOG_BASE_DIR` to `/app/logs`. Override them with `KAFKA_GROUP_ID`,
`KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_INPUT_TOPIC`, and `LOG_BASE_DIR` respectively.
A new group starts at the earliest retained record. Stop the old ingest instance
before deploying the new logger to avoid concurrent old/new exports.
