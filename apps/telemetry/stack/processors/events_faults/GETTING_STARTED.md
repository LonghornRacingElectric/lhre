# Getting started: events_faults

The [README](README.md) describes the proposed events and fault inputs.
The Kafka loop currently logs frames;
`detect_active_faults` is implemented but decoding, change detection, and event
publishing are still TODOs.

## Where the fault signals come from (decided)

The processor consumes fault signals from the generated CAN schema, independently
of `car_status`'s advisory `active_faults` list. The current detector accepts
`diagnostics_high` pedal/stomp booleans and inverter bitmasks as stand-in inputs.
When dedicated fault signals are defined, update the detector to use them.

The shared [lib/spec](../../../../../lib/spec/README.md) is the
schema source. Bazel generates the telemetry protobuf and Python bindings;
there is no checked-in CSV/protobuf regeneration workflow on this branch.
Follow the [telemetry codegen guide](../../../../../lib/codegen/telemetry/README.md)
and [BEVO schema notes](../../../../BEVO/schema/README.md) when changing fields.
Do not copy old CSVs or generated bindings from the source branch.

## Run the scaffold

From the repository root, with Docker running:

```bash
bazel build --config=local //apps/telemetry/stack/processors/events_faults:events_faults_binary
bazel run --config=local //apps/telemetry/stack/processors/events_faults:events_faults_load
./apps/telemetry/stack/server_devtool.sh up
./apps/telemetry/stack/server_devtool.sh enable events_faults
./apps/telemetry/stack/server_devtool.sh logs events_faults
```

`--config=local` avoids requiring a BuildBuddy key for the explicit Bazel
commands. The devtool also builds/loads the registered image before enabling
it. Compose runs the loaded tag with `--no-build`; reload it after source edits.
The core creates the external `telemetry_network` used by the processor.

A healthy start logs `events_faults ready. in=sensor_data out=fault_events`.
It starts at the latest offset for a new consumer group, so publish new frames
to see activity. No events are published yet. The existing consumer uses its
default automatic commits; reliable output delivery needs a deliberate
publish/acknowledgment and offset-commit strategy before deployment.

Environment variables are set in `docker-compose.yml`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker inside Docker |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Protobuf frames in |
| `KAFKA_OUTPUT_TOPIC` | `fault_events` | Proposed JSON events out |
| `KAFKA_GROUP_ID` | `events-faults-group` | Consumer group |
| `LOGLEVEL` | `INFO` | Set to `DEBUG` while investigating |

## Finish the channel

The TODOs in `main.py` mark the remaining work:

1. Decode the selected car's protobuf from `record.value`. Use the Kafka
   `car_type` header to avoid interpreting another car's payload as Orion.
2. Pass the diagnostics block to `detect_active_faults`. It returns stable
   fault names; it does not publish events.
3. Compare the active set with the previous set for that car. Publish one
   `fault_raised` for an added fault and one `fault_cleared` for a removed fault.
   Repeated unchanged state should publish nothing.

The existing
[car-status processor](../car_status/README.md) provides examples of protobuf
decoding and Kafka JSON publishing. Keep detector/change logic testable without
Kafka, and add a Bazel `py_test` when implementing those steps. Cover no faults,
a raised fault, repeated state, a cleared fault, and invalid bitmask inputs.

Once publishing works, exercise it with synthetic protobuf frames on an
isolated test broker. Inspect the resulting events with:

```bash
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:29092 --topic fault_events --from-beginning
```

The current scaffold will not create output for that consumer. Update the
README and this guide as the remaining steps become functional.
