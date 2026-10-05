# Board health processor

Starter project for new members. The processor is a **skeleton**: it connects to
Kafka and logs incoming frames, and the board-health logic is yours to write. You
will touch Kafka, protobuf decoding, and the Docker/Bazel wiring of a processor,
which is the shape of every other processor in this stack. New to it? Start
with [GETTING_STARTED.md](GETTING_STARTED.md).

## What it should do

Answer one question during a run: *is every board on the car's CAN bus still
talking?*

Orion's telemetry frames carry a `board_status` block (`OrionBoardStatus` in
`stack/kafka/proto/sensor/sensor.proto`) with one `<board>_last_seen_s` value for
each of `csm`, `dui`, `hvc`, `inverter`, `pdu`, `tsm`, `usm`, and `vcu`. The
name suggests "seconds since the board was last heard"; confirm that in the
firmware/schema (`apps/BEVO/schema`) before you build on it.

The finished processor reads those frames from the `sensor_data` topic, decides
per board whether it is stale, and publishes to `board_health` when a board
changes state:

```json
{"car": "orion", "board": "vcu", "state": "stale", "last_seen_s": 9.0, "t_ms": 1767225600000}
```

`board_health` is a new topic (nothing produces or consumes it yet), so the
event shape is yours to settle with whoever will read it.

## Quick start

Needs the core stack (Kafka) running first. From the repo root:

```bash
./apps/telemetry/stack/server_devtool.sh up                      # core stack, if not already up
./apps/telemetry/stack/server_devtool.sh enable board_health     # build + start this processor
./apps/telemetry/stack/server_devtool.sh logs board_health       # watch it work
```

You should see `board_health ready` and, once frames flow, a `frame #...` line
every 100 frames. Nothing flows until something publishes to `sensor_data`; the
processor starts at the latest offset and does not replay history. To see a
topic from the host (Kafka is published on `localhost:29092`):

```bash
python -c "
from kafka import KafkaConsumer
for m in KafkaConsumer('board_health', bootstrap_servers='localhost:29092'): print(m.value.decode())"
```

## Your task

The TODOs in `main.py` mark the steps:

1. **Decode.** Turn `record.value` into an `OrionSensorData` using the generated code in `stack/ingest/protobuf/`. `car_status/main.py` does this; note it picks the car from the `car_type` header.
2. **Detect.** Read `board_status` and decide which boards are stale. Start with one threshold; decide what a good value is and how you would find out (the boards' real send rates).
3. **Publish.** Emit an event when a board changes state, not on every frame.
4. **Test it.** Put the decision logic in a small pure function so it can be tested without Kafka, and add a `py_test`. `car_status/` (`classifier.py`, `test_classifier.py`, `BUILD.bazel`) is the example.

Things to figure out along the way:

- A frame with no `board_status` decodes to `0.0` for every board, which looks healthy. How should that be handled?
- Angelique and Nightwatch frames have no `board_status`. Skip them or handle them?
- What should the processor say about a board it has never seen?

Update this README when you finish: replace the task list with what the
processor actually does, and document any new environment variables.

## Configuration

Set in `docker-compose.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address inside `telemetry_network` |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Raw protobuf frames |
| `KAFKA_OUTPUT_TOPIC` | `board_health` | Where your events go |
| `KAFKA_GROUP_ID` | `board-health-group` | Consumer group |

## Files

`main.py` the skeleton. `Dockerfile` / `docker-compose.yml` the legacy Docker
path. `BUILD.bazel` the Bazel path: targets follow the standard
`board_health_binary`, `_image`, `_load`, `_push`, and `_smoke_test` contract. It
is optional and never part of `core_images`.
