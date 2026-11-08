# CSV logger

Reads Orion protobufs from `sensor_data` into `$LOG_BASE_DIR/orion`.
Columns follow the generated schema: `packet.<field>` or `<group>.<field>`;
arrays are JSON cells and absent groups are blank.

- Each Orion connection starts a new CSV; queued packets keep their session.
- New Kafka groups start at `latest`, avoiding retained-history exports.
- Restarts resume the session file. A durable checkpoint prevents duplicate
  rows after interrupted writes or Kafka commits. Keep it with the CSVs.
- Run one logger per log directory. Files are not automatically deleted;
  missing or truncated checkpointed files cause an explicit failure.

```bash
bazel build //apps/telemetry/stack/processors/csv_logger:csv_logger_image
bazel test //apps/telemetry/stack/processors/csv_logger:csv_logger_test
```

Deploy ingest, bridge, and logger together, then reconnect Orion. Packets without
session metadata are skipped. `server_devtool.sh build kafka ingest` loads and
starts the affected images; Compose mounts `apps/telemetry/logs`.

Defaults: group `telemetry-csv-logger-v1`, broker `kafka:9092`, topic
`sensor_data`, log root `/app/logs`. Override with `KAFKA_GROUP_ID`,
`KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_INPUT_TOPIC`, and `LOG_BASE_DIR`.
Existing groups resume committed offsets.
