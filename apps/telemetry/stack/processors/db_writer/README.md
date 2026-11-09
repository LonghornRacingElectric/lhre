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
or consumer. Artemis requires the `artemis_updates_v1` Kafka payload header,
decodes batches with explicit numeric presence, and writes only observed fields.
Raw identity is `(session_start_ms, time, sample_time_ns)`; receipt nanoseconds
preserve independent samples within one millisecond. Disjoint observations with
one key merge, identical replay is idempotent, and conflicting values roll back
the batch without committing Kafka offsets.

Recording windows split on gaps greater than five minutes. Their keys are
fixed time-derived values; start/end bounds can expand when older data arrives.
Late data matches historical windows and can merge windows when it closes a
gap. Drive days are associated through time rather than required before ingest.

Artemis has no packet-ID wire field, startup allocation or transport high-water
row. Missing fields remain SQL NULL, including fields beside recorded zero or
false values. Live-state reconstruction belongs to consumers, never history.
Orion retains its separate packet-based legacy integration.

Run the [explicit schema apply](../../../scripts/README.md) before restarting
this service for additions. No automatic schema service, compression policy,
retention deletion or continuous aggregate is introduced for the demo.
