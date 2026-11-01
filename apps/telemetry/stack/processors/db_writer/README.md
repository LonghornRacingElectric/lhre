# Database writer

`db_writer` persists raw `sensor_data` protobuf records independently of MQTT.
Kafka holds the backlog while Postgres is slow or unavailable. It is a required
core service, started by ingest's Compose project and loaded by `build ingest`.

```bash
bazel build //apps/telemetry/stack/processors/db_writer:db_writer_image
bazel test //apps/telemetry/stack/processors/db_writer:db_writer_test
bazel test //apps/telemetry/stack/kafka:kafka_bridge_test
```

## Transactions and replay

Each bounded batch is decoded using its required `car_type` header (`Nightwatch`,
`Angelique`, or `Orion`). Each car gets one transaction: insert all packet rows,
then bulk insert each populated sensor table, update session windows, and save
`public.sensor_ingest_checkpoint`. Statements are chunked to bound parameters.
The checkpoint table is created on startup, including on existing volumes; the
service account needs CREATE permission on `public` for its first start.

Kafka offsets advance only after every car transaction succeeds. A crash after
one database commits, or before the Kafka commit, replays safely using the
transactional checkpoints. Commits use synchronous WAL durability. A per-database
advisory lock serializes writer transactions and session metadata across group
rebalances. Statements time out after 30 seconds; lock waits after 10 seconds.

Duplicate packet IDs with the same timestamp are first-writer-wins: the entire
incoming packet, including its child rows, is skipped. This handles uncertain
bridge retries and overlap with pre-cutover data or completed logsync imports,
including databases without unique child constraints. It does not repair an
already incomplete packet. Different timestamps for an existing ID, conflicting
packets in one batch, invalid protobuf, missing/unknown headers, and oversized
records block processing with an error. Nothing is silently acknowledged past
that batch; operators must resolve the input/schema problem before retrying.

Failed work retries from committed Kafka offsets after five seconds, with no
second application backlog. The consumer closes without auto-commit on failure.
SIGTERM flushes pending work; a forced stop safely replays it. Processing that
loses group membership may finish its database transaction, but cannot advance
Kafka offsets for a new owner. Checkpoints make that replay safe.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Comma-separated brokers |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Raw protobuf topic |
| `KAFKA_GROUP_ID` | `telemetry-db-writer-v1` | Stable consumer/checkpoint identity |
| `DB_BATCH_PACKETS` | `500` | Maximum records per transaction batch |
| `DB_BATCH_BYTES` | `8388608` | Maximum buffered protobuf bytes; one larger record blocks |
| `DB_BATCH_SECONDS` | `0.25` | Maximum accumulation time from the first buffered record |

The time limit also applies during continuous traffic. It bounds accumulation,
not database or Kafka commit time. Polling fetches at most one count-bounded
batch in addition to the pending batch. Decoding adds bounded per-batch memory;
there is no in-memory queue proportional to Kafka lag. Three car databases use
the existing `electric` connection settings and `ELECTRIC_PWD`.

One consumer runs sequentially. `sensor_data` currently has one partition, so
extra replicas provide failover rather than more throughput. Measure batch
latency, database errors, consumer lag (`kafka-consumer-groups.sh --describe`),
oldest retained offsets, and disk use before tuning. Catch-up throughput must
exceed incoming throughput; Kafka retention remains a finite outage budget.
The [bridge](../../kafka/README.md) owns raw-topic retention settings.

## Cutover and recovery

1. Stop the old ingest service and allow its database/bridge queues to drain
   before stopping its process. The old daemon workers do not guarantee a drain
   on forced shutdown; stop producers first and verify database/Kafka counts.
2. Verify retained `sensor_data` records have valid `car_type` headers and the
   deployed schema. Back up the database and record topic offsets. Resolve
   incomplete historical packets before enabling first-writer-wins admission.
3. Build/load ingest, bridge, and writer together. Start the new bridge and writer
   before resuming producers. Do not leave an old protobuf DB writer running.
4. A new group starts at the oldest retained Kafka offset. Existing complete
   packets are skipped and new packets are inserted. Check lag reaches zero and
   validate sensor rows before resuming normal operation.

For an existing group, offsets older than Kafka retention cause an explicit
failure rather than resetting forward. Restore missing data from car logs and
perform an explicit operator-controlled offset reset only after reconciliation.
Do not delete checkpoints or change group identities as a routine retry. Do not
recreate the raw topic under the same name while retaining its checkpoints.
Rolling back to the old direct writer requires stopping the Kafka writer first
and reconciling any backlog; running both writers is unsupported.

## Database integration tests

Use a disposable Postgres server whose account can create/drop databases. The
test creates uniquely named databases and removes only those databases.
It checks all cars, parent/child integrity, replay without child uniqueness,
partial mixed-car commits, failed child inserts, and session gaps.

```bash
bazel test --config=local //apps/telemetry/stack/processors/db_writer:postgres_test \
  --test_env=POSTGRES_TEST_URL --test_output=errors
```

`POSTGRES_TEST_URL` is a SQLAlchemy `postgresql+psycopg2://.../postgres` URL.
The normal unit target requires neither Docker nor credentials.

Set `KAFKA_TEST_BROKERS` as a second test environment variable to also exercise
real Kafka backlog consumption, explicit offset commits, and restart after a
simulated crash between database and Kafka commits. The test uses a unique topic
and consumer group and deletes its topic afterwards.
