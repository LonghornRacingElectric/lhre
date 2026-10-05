# Events and faults processor

Starter project for new members. The processor is a **skeleton**: it connects to
Kafka and logs incoming frames, and the fault-event logic is yours to write. You
will touch Kafka, protobuf decoding, and the Docker/Bazel wiring of a processor,
which is the shape of every other processor in this stack. New to it? Start
with [GETTING_STARTED.md](GETTING_STARTED.md).

## What it should do

Turn the car's fault signals into discrete *raised* / *cleared* events, so
something downstream can react to "a fault just happened" instead of polling
every frame.

It reads frames from the `sensor_data` topic. The signals to start from are in
Orion's `diagnostics_high` block (`OrionDiagnosticsHigh` in
`stack/kafka/proto/sensor/sensor.proto`):

- `post_faults` and `run_faults`: inverter fault bitmasks, one bit per fault.
- Pedal-sensor booleans ending in `_disconnect`, `_out_range`, `_implause`, or `_mismatch` (APPS / BPPS / BSE).
- `stomp_fault`.

The finished processor keeps the set of active faults and publishes to
`fault_events` whenever that set changes:

```json
{"kind": "fault_raised", "fault": "run_faults[2]", "car": "orion", "t_ms": 1767225600000}
```

`fault_events` is a new topic (nothing produces or consumes it yet), so the
event shape is yours to settle with whoever will read it.

"Events" here means fault events. Race start/end (`event_id`) travels over MQTT
and is handled by `ingest`; no events or faults topic exists on Kafka today.

**Input signals (decided):** this processor consumes new CAN fault signals that
come out of SDD generation. It does not reuse `car_status`'s `active_faults`.
Those signals will show up in the generated schema and SDD code once they are
produced. Until then, the `diagnostics_high` fields above are the stand-in. See
[GETTING_STARTED.md](GETTING_STARTED.md#where-the-fault-signals-come-from-decided).

## Quick start

Needs the core stack (Kafka) running first. From the repo root:

```bash
./apps/telemetry/stack/server_devtool.sh up                      # core stack, if not already up
./apps/telemetry/stack/server_devtool.sh enable events_faults    # build + start this processor
./apps/telemetry/stack/server_devtool.sh logs events_faults      # watch it work
```

You should see `events_faults ready` and, once frames flow, a `frame #...` line
every 100 frames. Nothing flows until something publishes to `sensor_data`; the
processor starts at the latest offset and does not replay history. To see a
topic from the host (Kafka is published on `localhost:29092`):

```bash
python -c "
from kafka import KafkaConsumer
for m in KafkaConsumer('fault_events', bootstrap_servers='localhost:29092'): print(m.value.decode())"
```

## Your task

The TODOs in `main.py` mark the steps:

1. **Decode.** Turn `record.value` into an `OrionSensorData` using the generated code in `stack/ingest/protobuf/`. `car_status/main.py` does this; note it picks the car from the `car_type` header.
2. **Detect.** Read `diagnostics_high` and work out which faults are active.
3. **Diff and publish.** Compare with the previous frame and emit raised/cleared events only on change.
4. **Test it.** Put the detect/diff logic in a small pure function so it can be tested without Kafka, and add a `py_test`. `car_status/` (`classifier.py`, `test_classifier.py`, `BUILD.bazel`) is the example.

Things to figure out along the way:

- `post_faults` / `run_faults` are `float32` in the proto, which cannot hold more than 24 bits exactly. Which bits can you trust?
- Which bit is which fault? Find the inverter's fault table so events have names instead of `run_faults[2]`.
- For the fuse, contactor, and shutdown booleans, does `true` mean ok or tripped? Nothing documents it; find out before including them.
- Angelique's `current_errors` / `latching_faults` are opaque bytes/JSON. Skip them or reverse-engineer them?
- What happens when a sensor flickers? You will get a raised/cleared pair per flicker unless you debounce.

Update this README when you finish: replace the task list with what the
processor actually does, and document any new environment variables.

## Configuration

Set in `docker-compose.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address inside `telemetry_network` |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Raw protobuf frames |
| `KAFKA_OUTPUT_TOPIC` | `fault_events` | Where your events go |
| `KAFKA_GROUP_ID` | `events-faults-group` | Consumer group |

## Files

`main.py` the skeleton. `Dockerfile` / `docker-compose.yml` the legacy Docker
path. `BUILD.bazel` the Bazel path: targets follow the standard
`events_faults_binary`, `_image`, `_load`, `_push`, and `_smoke_test` contract. It
is optional and never part of `core_images`.
