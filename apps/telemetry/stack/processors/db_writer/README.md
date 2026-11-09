# Database writer

Reads raw `sensor_data` protobuf using its own Kafka consumer group. Records are
grouped by car and inserted in one transaction per car. Artemis uses generated
frequency pools and session bounds; Orion retains packet/sensor tables. Kafka offsets are committed after the database transactions succeed.
Failed inserts roll back and retry from Kafka after five seconds.

For Orion, packet IDs use `ON CONFLICT DO NOTHING`; sensor rows are written only for newly
inserted packets. This handles replay when the database commit succeeds before
the Kafka commit. Existing packets are first-writer-wins, including their sensor
rows. Artemis replay handling is described below.

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

## Artemis frequency pools

This remains the single Kafka database-writer service. `ingest/pool_storage.py`
is a SQLAlchemy persistence helper called by this consumer, not another service
or consumer. Artemis snapshots are split by the generated pool catalogue and
written atomically with session bounds and transport high-water state. The
snapshot timestamp, not packet ID, identifies measurements. Replayed identical
samples are accepted; conflicting values at the same session/pool/time roll
back the batch and leave Kafka offsets uncommitted.

Recording windows split on gaps greater than five minutes. Their keys are
fixed time-derived values; start/end bounds can expand when older data arrives.
Late data matches historical windows and can merge windows when it closes a
gap. Drive days are associated through time rather than required before ingest.

The producer's packet ID remains only in the existing wire handshake and a
single transport high-water row. It is never a sensor relationship. Artemis
accepts timestamped protobuf messages without a positive packet ID; Orion
retains its positive packet-ID requirement. Current full snapshots retain
every received value, including repeated slow-channel values. Independent BEVO
pool sampling is a separate producer change.

Run the [explicit schema apply](../../../scripts/README.md) before restarting
this service for additions. No automatic schema service, compression policy,
retention deletion or continuous aggregate is introduced for the demo.
