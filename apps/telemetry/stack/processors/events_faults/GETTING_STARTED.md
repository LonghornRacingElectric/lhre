# Getting started: events_faults

The [README](README.md) describes the fault inputs and four-field event format.

## Follow the implementation

1. `main.py` reads the `car_type` header and decodes Orion protobuf frames.
2. `faults.py` detects asserted diagnostics flags and inverter bits.
3. `diff_faults` compares sets: `current - previous` raises faults and
   `previous - current` clears faults. Unchanged readings produce no events.
4. `main.py` publishes the events, waits for acknowledgments, then remembers
   the new fault set and commits the input offsets.

The tests run without Kafka, Docker, or a car:

```bash
bazel test --config=local //apps/telemetry/stack/processors/events_faults:faults_test //apps/telemetry/stack/processors/events_faults:main_test
```

They cover clean state, each supported boolean, inverter bits, repeated state,
raised/cleared transitions, actual protobuf decoding, unsupported cars, invalid
inputs, and publication failures.

## Where the fault signals come from (decided)

The detector reads explicit diagnostics signals independently of `car_status`'s
advisory fault list. [lib/spec](../../../../../lib/spec/README.md) is authoritative;
Bazel generates the protobuf and Python bindings. Follow the
[telemetry codegen guide](../../../../../lib/codegen/telemetry/README.md) and
[BEVO schema notes](../../../../BEVO/schema/README.md) when changing fields.

## Run against Kafka

With Docker running, from the repository root:

```bash
./apps/telemetry/stack/server_devtool.sh up
./apps/telemetry/stack/server_devtool.sh enable events_faults
./apps/telemetry/stack/server_devtool.sh logs events_faults
```

Inspect JSON events:

```bash
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:29092 --topic fault_events --from-beginning
```

Use a separate test broker for synthetic frames. Each Orion message needs a
`car_type: Orion` header, a `time` in milliseconds, and a populated
`diagnostics_high` block. An explicit empty block represents healthy state.
Send a fault, repeat it, and then clear it: expect one raised and one cleared
event. A new consumer group starts at the latest offset, so send frames after
it connects.

Fault state is local to the process and is not restored after restart.
