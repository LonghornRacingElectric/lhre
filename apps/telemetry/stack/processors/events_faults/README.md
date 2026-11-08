# Events and faults processor

Reads Orion telemetry from Kafka's `sensor_data` topic and publishes
`fault_raised` / `fault_cleared` JSON events to `fault_events` when the active
fault set changes. Repeated unchanged state produces no event.

New to the processor? Start with [GETTING_STARTED.md](GETTING_STARTED.md).

## Fault inputs

The detector reads the generated `OrionSensorData.diagnostics_high` block:

- APPS/BPPS/BSE disconnect, out-of-range, mismatch, and implausibility booleans.
- `stomp_fault`.
- Bits 0–23 of the inverter `post_faults` and `run_faults` masks, named by field
  and bit index (for example, `run_faults[2]`). Inverter bit names are not
  available in this repo.

Fuse, contactor, and shutdown/status booleans are excluded because their
asserted polarity is not documented. Inverter masks use float32 on the wire;
negative, fractional, non-finite, or values at least `2**24` reject the frame
because the low bits cannot be trusted. The previous fault set is preserved.

Inputs must have a `car_type: Orion` header. Other cars, untagged frames,
malformed protobuf, and missing diagnostics are skipped. An explicit empty
diagnostics block means no faults; an absent block does not clear faults.

## Events

```json
{"kind":"fault_raised","fault":"run_faults[2]","car":"orion","t_ms":1767225600000}
```

`fault_cleared` uses the same four fields. `t_ms` comes from the telemetry
packet's `time`, in milliseconds. Each frame compares the current fault set
with the previous set: added faults raise events and removed faults clear them.
The first valid frame starts from an empty set and raises any asserted faults.

The processor waits for each published event to be acknowledged before
remembering the new fault set and committing input offsets. Publishing failures
exit the process. Fault state is in memory, so restarting can announce active
faults again; there is no persistent fault history or debounce.

Fault definitions come from [lib/spec](../../../../../lib/spec/README.md),
independently of `car_status`'s advisory `active_faults` list. The diagnostics
fields above are the stand-in for future dedicated CAN fault signals; map new
signals in `faults.py` after they are added through the shared schema workflow.
Race start/end events remain part of ingest, not this processor.

## Run and test

Needs the core Kafka stack running. From the repository root:

```bash
./apps/telemetry/stack/server_devtool.sh up
./apps/telemetry/stack/server_devtool.sh enable events_faults
./apps/telemetry/stack/server_devtool.sh logs events_faults
```

A new consumer group starts at the latest offset; an existing group resumes its
committed position. Publish new Orion frames after startup. The output topic
must exist or broker topic auto-creation must be enabled.

The detection, transition, and decode/publish tests need no services:

```bash
bazel test --config=local //apps/telemetry/stack/processors/events_faults:faults_test //apps/telemetry/stack/processors/events_faults:main_test
```

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

- `main.py`: decode Orion frames, compare active faults, and publish changes.
- `faults.py`: pure fault detection and set comparison.
- `test_faults.py` / `test_main.py`: detection, transitions, and decode/publish tests.
- `BUILD.bazel`: standard `events_faults_binary`, `_image`, `_load`, `_push`, and
  `_smoke_test` targets, plus the test targets. The processor is optional.
- `docker-compose.yml`: runs the Bazel-loaded image. The old Dockerfile remains
  for legacy use; Bazel is the supported image build path.
