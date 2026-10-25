# Getting started: board_health

A walkthrough for picking this processor up cold. [README.md](README.md) is
the spec: what the output should look like and the open questions. This page
covers getting it running and getting unstuck.

## What it is and why

During a run, someone watching telemetry needs to know right away when a board
drops off the CAN bus. Scrolling eight `<board>_last_seen_s` graphs is too
slow for that. `board_health` watches those values and publishes an event
only when a board goes stale or comes back.

It is a Kafka processor like every other one in `stack/processors/`:

```
car ──MQTT──▶ ingest ──▶ kafka-bridge ──▶ [sensor_data] ──▶ board_health ──▶ [board_health]
```

- **Reads** `sensor_data`: one protobuf-encoded `OrionSensorData` per Kafka
  message, with a `car_type` header (`Orion`, `Angelique`, ...).
- **Writes** `board_health`: small JSON events. The topic is new, so nothing
  reads it yet.

## Run it locally

You need Docker, and Bazel through `bazelisk`. From the repo root:

```bash
./apps/telemetry/stack/server_devtool.sh up                    # core stack: kafka, ingest, db, ...
./apps/telemetry/stack/server_devtool.sh enable board_health   # bazel-build the image, then compose up
./apps/telemetry/stack/server_devtool.sh logs board_health     # Ctrl-C detaches; it keeps running
```

`enable` wraps two steps you can also run by hand once you change code:

```bash
bazel run --config=local //apps/telemetry/stack/processors/board_health:board_health_load   # build + load the image
cd apps/telemetry/stack/processors/board_health && docker compose up -d      # start it
```

Every `bazel` command here uses `--config=local`. The repo's default config
builds on the team's BuildBuddy cluster, which needs an API key in
`.bazelrc.user`. Without one you get `UNAUTHENTICATED: User not found` buried
in a Java stack trace. `--config=local` builds on your machine instead; the
first build takes a few minutes.

`docker-compose.yml` has no `build:` section. It runs whatever
`lhre/telemetry-board-health:dev` image was loaded last, so reload after every
change or you are testing old code. It also joins the external
`telemetry_network`, which only exists after the core stack is up.

Environment variables, all set in `docker-compose.yml`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker inside Docker. From your host it is `localhost:29092`. |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Protobuf frames in |
| `KAFKA_OUTPUT_TOPIC` | `board_health` | JSON events out |
| `KAFKA_GROUP_ID` | `board-health-group` | Consumer group |
| `LOGLEVEL` | `INFO` | `DEBUG` while you work |

A healthy start logs `board_health ready. in=sensor_data out=board_health`.
The processor reads from the *latest* offset, so it sits silent until
something new is published. See the hints below for how to feed it.

## What you fill in

Everything to write is in `main.py`, marked `TODO 1`–`3` inside the poll loop.
The setup code around them already works.

1. **Decode**: `record.value` into an `OrionSensorData`.
   [`car_status/main.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/telemetry/stack/processors/car_status/main.py)
   does this in `_car_from_headers` and `_decode`.
2. **Detect**: read `board_status` and decide which boards are stale. The
   fields are listed in
   [`apps/BEVO/schema/can_packets.proto`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/BEVO/schema/can_packets.proto)
   (`message BoardStatus`).
3. **Publish**: when a board changes state, send an event to `OUTPUT_TOPIC`.
   `car_status`'s `_emit` shows a `KafkaProducer` with a JSON serializer.

Also expected:

- **A test.** Put the stale/changed decision in a pure function in its own
  file, with no Kafka in it, and add a `py_test` in `BUILD.bazel`.
  `car_status/` has the pattern: `classifier.py`, `test_classifier.py`, and
  their `BUILD.bazel` entries.
- **A README update.** Once it works, replace the task list in README.md with
  what the processor actually does.

Keep the Kafka plumbing out of the test. If the decision logic needs a broker
to test, it is in the wrong place.

## Hints for getting unstuck

**See what's on a topic.** Use the console consumer that ships in the Kafka
container. No install needed:

```bash
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:29092 --topic board_health --from-beginning
```

`sensor_data` is binary protobuf, so reading it that way prints garbage. To
check that frames are arriving at all, add `--property print.headers=true` and
look for `car_type:Orion`. To list every topic, run
`docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:29092 --list`.

**No car, no CAN hardware?** You don't need either.

- *Unit test first.* The pure function from "What you fill in" takes a
  `board_status` value, or a plain dict, and returns a decision. Build inputs
  by hand, e.g. one board at 0.2 s, one at 9 s, one missing, and run
  `bazel test --config=local //apps/telemetry/stack/processors/board_health/...`. That covers
  most of the logic without Docker.
- *Fake frames on the real pipeline.* Publish your own `OrionSensorData` to
  `sensor_data` from the host with the same header ingest adds. Run from
  `apps/telemetry/` so `stack.ingest.protobuf` imports:

  ```bash
  cd apps/telemetry && uv run --no-project --with kafka-python --with 'protobuf>=7.35' python -c "
  from kafka import KafkaProducer
  from stack.ingest.protobuf import can_packets_pb2
  f = can_packets_pb2.OrionSensorData(packet_id=1)
  f.board_status.vcu_last_seen_s = 9.0
  KafkaProducer(bootstrap_servers='localhost:29092').send(
      'sensor_data', f.SerializeToString(), headers=[('car_type', b'Orion')]).get(10)"
  ```

  Change the numbers between sends to walk a board from fresh to stale and
  back, then check your `logs` and the `board_health` topic.
  [`stack/tests/test_data_flow.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/telemetry/stack/tests/test_data_flow.py)
  does the same thing at a larger scale.

**Import errors on `can_packets_pb2`, or a field that isn't there?** The
checked-in Python bindings can lag the schema. Regenerate them from the
current proto:

```bash
bazel run --config=local //apps/telemetry/stack/ingest:update_can_packets_pb2
```

This only matters for code you run on your host, like the fake-frame snippet
above. The image that `enable` builds always gets freshly generated bindings.
Don't commit the regenerated file as part of your processor work.

**Nothing in the logs at all?** Check `server_devtool.sh status`. Kafka has to
be healthy before the processor starts, and a processor that crashed on import
shows up as `Exited`.
