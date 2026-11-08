# Events and faults processor

Consumes Orion telemetry from Kafka's `sensor_data` topic and publishes JSON
`fault_raised` / `fault_cleared` events to `fault_events`. Repeated unchanged
fault state produces no event. This is an optional live processor; it does not
write to PostgreSQL or change the telemetry schema.

See [GETTING_STARTED.md](GETTING_STARTED.md) for the demo and development flow.

## Inputs and detection

Only messages with one explicit `car_type: Orion` header are decoded. Other
cars, missing/ambiguous car tags, malformed protobuf, missing diagnostics, and
non-positive timestamps are skipped. Invalid frames are logged and their input
offsets can advance; there is no dead-letter topic.

The generated `OrionSensorData.diagnostics_high` supplies:

- APPS/BPPS/BSE disconnect, out-of-range, mismatch, and implausibility booleans.
- `stomp_fault`.
- Individual bits 0–23 of `post_faults` and `run_faults`, named by field and bit
  index (for example, `run_faults[2]`). Inverter bit-name definitions are not
  available here.

Fuse, contactor, and shutdown/status booleans are excluded because their asserted
polarity is not documented. Inverter masks are float32 on the wire. Negative,
fractional, non-finite, or values at least `2**24` reject the whole frame rather
than clearing previously active faults using rounded or invalid data.

A valid frame is a complete diagnostics snapshot. An absent diagnostics block
never means that all faults cleared. Frames older than the latest accepted
telemetry timestamp are ignored. Equal timestamps are allowed. Event `t_ms`
is the packet's timestamp in milliseconds, not the precise CAN transition time;
it is never replaced with server wall-clock time.

## Events

```json
{"kind":"fault_raised","fault":"stomp_fault","car":"orion","t_ms":1767225601000,"packet_id":2,"event_id":"sensor_data:0:1:fault_raised:stomp_fault"}
```

`fault_cleared` uses the same fields. Kafka's message key is `orion`, keeping
output for the car on one partition. `event_id` combines input topic, partition,
offset, event kind, and fault name, so retries of the same transition have the
same identity. It is not a global CAN sample ID. Consumers can deduplicate by
this field.

Each frame compares the detected fault set with the previously accepted set.
The first valid frame starts from an empty set and raises every asserted fault.
Events are ordered by kind (raised before cleared), then fault name.

## Delivery and limits

The producer requires `acks=all`, retries failed sends, and waits for each send
acknowledgment before input offsets are committed. A publish or commit failure
exits the process; Compose restarts it. A crash between publishing and committing
can produce duplicates. This is not exactly-once delivery; see the
[Kafka delivery semantics](https://kafka.apache.org/42/design/design/).

Fault state is held in memory. Restarting can announce existing active faults
again, and cannot reconstruct a cleared transition when its previous active
state was lost. Do not use this stream as a durable fault ledger or as the only
source of an authoritative current-fault display. Persistent state and recovery
across restarts/rebalances are future work. Run one processor instance with the
current one-partition input topic; distributing a car's frames over partitions
is not supported. No debounce, heartbeat, or fault-event database is provided.

Fault definitions are generated from [lib/spec](../../../../../lib/spec/README.md).
The detector is independent of `car_status`'s advisory `active_faults` list.
Dedicated future CAN fault signals should be added through the shared schema
workflow, then mapped in `faults.py`.

## Run and verify

From the repository root:

```bash
# No Kafka, Docker, database, or car required.
bazel test --config=local //apps/telemetry/stack/processors/events_faults:faults_test //apps/telemetry/stack/processors/events_faults:processor_test
bazel run --config=local //apps/telemetry/stack/processors/events_faults:demo

# Live processor: requires the core stack and its Kafka broker.
./apps/telemetry/stack/server_devtool.sh up
./apps/telemetry/stack/server_devtool.sh enable events_faults
./apps/telemetry/stack/server_devtool.sh logs events_faults
```

For a new consumer group, consumption starts at the latest offset. Existing
groups resume their committed position. Publish new Orion frames after startup.
The output topic must already exist or broker topic auto-creation must be enabled.
The source topic is also used by real telemetry; synthetic broker demos belong
on a separate test broker or isolated input/output topics.

Container targets follow the `events_faults_binary`, `_image`, `_load`, `_push`,
and `_smoke_test` contract. Compose runs Bazel-loaded tags; reload after edits.
The old Dockerfile is retained for legacy use and is not the supported build path.

## Configuration

Set these in `docker-compose.yml`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Raw protobuf frames |
| `KAFKA_OUTPUT_TOPIC` | `fault_events` | JSON transition events |
| `KAFKA_GROUP_ID` | `events-faults-group` | Consumer group |
| `LOGLEVEL` | `INFO` | Logging verbosity |

## Files

- `faults.py`: pure fault detection and set comparison.
- `processor.py`: header validation, generated protobuf decoding, acknowledged
  publishing, and batch offset commits.
- `main.py`: Kafka configuration, polling, and shutdown.
- `test_faults.py` / `test_processor.py`: detection, decoding, transitions,
  skipped input, timestamp ordering, and publish-failure checks.
- `demo.py`: real serialized protobuf inputs with a printing producer stub;
  demonstrates the application logic, not Kafka broker delivery.
