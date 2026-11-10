# Events and faults processor

Reads Artemis native updates from Kafka's `sensor_data` topic and publishes
`fault_raised` / `fault_cleared` JSON events to `fault_events` when the active
fault set changes. Repeated unchanged state produces no event.

New to the processor? Start with [GETTING_STARTED.md](GETTING_STARTED.md).

## Fault inputs

The detector reads the generated `ArtemisSensorData.diagnostics_high` fields:

- APPS/BPPS/BSE disconnect, out-of-range, mismatch, and implausibility booleans.
- `stomp_fault`.
- Bits 0–23 of the inverter `post_faults` and `run_faults` masks, named by field
  and bit index (for example, `run_faults[2]`). Inverter bit names are not
  available in this repo.

Fuse, contactor, and shutdown/status booleans are excluded because their
asserted polarity is not documented. Inverter masks use float32 on the wire;
negative, fractional, non-finite, or values at least `2**24` reject the frame
because the low bits cannot be trusted. The previous fault set is preserved.

Inputs must have `car_type: Artemis` and `payload_format: artemis_updates_v1`
headers. The shared decoder validates each `TelemetryBatch` and extracts only
fields listed in `updated_fields`. Other cars, untagged records, and malformed
batches are skipped. Unobserved diagnostics retain their last values; only an
explicit false flag or zero mask clears the corresponding faults. Updates to
other groups do not change fault state.

## Events

```json
{"kind":"fault_raised","fault":"run_faults[2]","car":"artemis","t_ms":1767225600000}
```

`fault_cleared` uses the same four fields. `t_ms` comes from the telemetry
update's BEVO `time`, in milliseconds. Each update compares the current fault set
with the previous set: added faults raise events and removed faults clear them.
The first observed diagnostics start from an empty set and raise any asserted
faults. Multiple updates in one batch are processed in order with their own
timestamps. There is no packet-ID dependency.

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
committed position. Publish new Artemis batches after startup. The output topic
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
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Artemis protobuf batches |
| `KAFKA_OUTPUT_TOPIC` | `fault_events` | JSON transition events |
| `KAFKA_GROUP_ID` | `events-faults-group` | Consumer group |
| `LOGLEVEL` | `INFO` | Logging verbosity |

## Files

- `main.py`: decode Artemis updates, retain diagnostics, and publish changes.
- `faults.py`: pure fault detection and set comparison.
- `test_faults.py` / `test_main.py`: detection, transitions, and decode/publish tests.
- `BUILD.bazel`: standard `events_faults_binary`, `_image`, `_load`, `_push`, and
  `_smoke_test` targets, plus the test targets. The processor is optional.
- `docker-compose.yml`: runs the Bazel-loaded image using the shared telemetry
  requirements and generated schema bindings.
