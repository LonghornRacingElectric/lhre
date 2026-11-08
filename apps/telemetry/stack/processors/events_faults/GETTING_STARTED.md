# Getting started: events_faults

Start with the demo, then follow the three implementation files. The
[README](README.md) describes the event contract and delivery limits.

## Demo without Kafka

From the repository root:

```bash
bazel run --config=local //apps/telemetry/stack/processors/events_faults:demo
```

The demo serializes six actual Orion protobuf frames and passes them through
`FaultProcessor` with a producer stub that prints JSON. It does not connect to
a broker. The sequence is:

| Frame | Diagnostics | Output |
| --- | --- | --- |
| 1 | No active faults | None |
| 2 | Stomp fault active | `fault_raised: stomp_fault` |
| 3 | Same fault still active | None |
| 4 | Stomp fault clear | `fault_cleared: stomp_fault` |
| 5 | Inverter run mask `4` | `fault_raised: run_faults[2]` |
| 6 | Inverter run mask `0` | `fault_cleared: run_faults[2]` |

## Follow the implementation

1. `faults.py`: `detect_active_faults` reads diagnostics and returns fault names.
   `diff_faults` compares sets: `current - previous` raises faults and
   `previous - current` clears faults.
2. `processor.py`: validate the car header, decode generated `OrionSensorData`,
   and reject missing/invalid diagnostics or timestamps. Send changes as JSON
   events, wait for acknowledgments, then remember the accepted state. After
   the batch succeeds, commit its Kafka offsets.
3. `main.py`: configure the consumer/producer and poll until SIGTERM or SIGINT.
   A publishing failure propagates and exits without advancing input offsets.

Tests run without any services:

```bash
bazel test --config=local //apps/telemetry/stack/processors/events_faults:faults_test //apps/telemetry/stack/processors/events_faults:processor_test
```

The processor tests serialize real generated protobufs and use mocked Kafka
clients. They check duplicate state, raised/cleared transitions, malformed and
non-Orion input, invalid masks, older frames, acknowledgment ordering, and
publish failures.

## Where the fault signals come from (decided)

The detector reads the schema's explicit diagnostics signals, independently
of `car_status`'s advisory fault list. [lib/spec](../../../../../lib/spec/README.md)
is authoritative; Bazel generates the protobuf and Python bindings.
Follow the [telemetry codegen guide](../../../../../lib/codegen/telemetry/README.md)
and [BEVO schema notes](../../../../BEVO/schema/README.md) when changing fields.
Do not copy old CSVs or generated bindings into the source tree.

## Run against Kafka

With the core running, enable the processor:

```bash
./apps/telemetry/stack/server_devtool.sh enable events_faults
./apps/telemetry/stack/server_devtool.sh logs events_faults
```

Inspect live JSON events:

```bash
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:29092 --topic fault_events --from-beginning
```

A new group starts at the latest offset, so send new frames after it connects.
Each Orion input needs one `car_type: Orion` header, a positive `time` in
milliseconds, and a populated `diagnostics_high` block. An explicit empty
block represents healthy state. Use an isolated broker/topics for fake data.

State is local to the process and is not restored after restart. This demo
proves live transitions and acknowledgment ordering; it does not prove durable
fault history, replay correctness after losing state, or production readiness.
