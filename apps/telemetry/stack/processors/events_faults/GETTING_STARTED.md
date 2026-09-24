# Getting started: events_faults

A walkthrough for picking this processor up cold. [README.md](README.md) is
the spec: what the output should look like and the open questions. This page
covers getting it running and getting unstuck.

## What it is and why

The car reports faults as state: a bit or a boolean that stays set for as
long as the fault is active, repeated in every frame. People and tools
downstream want *events* instead, like "APPS mismatch raised at 12:03:07" and
"cleared at 12:03:09", so they can alert, log, or mark a lap without
re-scanning every frame. `events_faults` turns fault state into those
raised/cleared events.

It is a Kafka processor like every other one in `stack/processors/`:

```
car ──MQTT──▶ ingest ──▶ kafka-bridge ──▶ [sensor_data] ──▶ events_faults ──▶ [fault_events]
```

- **Reads** `sensor_data`: one protobuf-encoded `OrionSensorData` per Kafka
  message, with a `car_type` header (`Orion`, `Angelique`, ...).
- **Writes** `fault_events`: small JSON events. The topic is new, so nothing
  reads it yet.

### Where the fault signals come from (decided)

`events_faults` consumes **new CAN fault signals produced by SDD generation**.
It does **not** reuse or re-derive `car_status`'s `active_faults` list. The two
processors have separate jobs: `car_status` sums up the car's overall state,
and `events_faults` owns the fault event stream.

The new signals don't exist yet. Once SDD generation produces them, they show
up in the generated schema and code like every other signal:

- `OrionSensorData` in
  [`apps/BEVO/schema/can_packets.proto`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/BEVO/schema/can_packets.proto),
  and so in the Python bindings (`stack/ingest/protobuf/can_packets_pb2.py`)
  that `main.py` decodes with. See the [BEVO schema README](../../../../BEVO/schema/README.md).
- The SDD artifacts (dataclasses, ORM models, Prisma) that
  `scripts/sync_schema.sh` generates. See [SDD Usage](../../../scripts/docs/SDD_USAGE.md).

Until then, build against the existing `diagnostics_high` fields (`run_faults`,
`post_faults`, the pedal booleans, `stomp_fault`). That is why `main.py`'s
TODO 2 names `diagnostics_high`. Decoding, diffing, publishing and tests are
the same whichever fields feed them. When the generated signals land,
switching over should only change which fields your detect step reads. Keep
that step in one small function so the switch stays that small. To see how a
signal gets from a definition to a field on `OrionSensorData`, do the
[end-to-end exercise](#end-to-end-where-do-these-signals-come-from) below.

## Run it locally

You need Docker, and Bazel through `bazelisk`. From the repo root:

```bash
./apps/telemetry/stack/server_devtool.sh up                     # core stack: kafka, ingest, db, ...
./apps/telemetry/stack/server_devtool.sh enable events_faults   # bazel-build the image, then compose up
./apps/telemetry/stack/server_devtool.sh logs events_faults     # Ctrl-C detaches; it keeps running
```

`enable` wraps two steps you can also run by hand once you change code:

```bash
bazel run --config=local //apps/telemetry/stack/processors/events_faults:events_faults_load   # build + load the image
cd apps/telemetry/stack/processors/events_faults && docker compose up -d       # start it
```

Every `bazel` command here uses `--config=local`. The repo's default config
builds on the team's BuildBuddy cluster, which needs an API key in
`.bazelrc.user`. Without one you get `UNAUTHENTICATED: User not found` buried
in a Java stack trace. `--config=local` builds on your machine instead; the
first build takes a few minutes.

`docker-compose.yml` has no `build:` section. It runs whatever
`lhre/telemetry-events-faults:dev` image was loaded last, so reload after
every change or you are testing old code. It also joins the external
`telemetry_network`, which only exists after the core stack is up.

Environment variables, all set in `docker-compose.yml`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker inside Docker. From your host it is `localhost:29092`. |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Protobuf frames in |
| `KAFKA_OUTPUT_TOPIC` | `fault_events` | JSON events out |
| `KAFKA_GROUP_ID` | `events-faults-group` | Consumer group |
| `LOGLEVEL` | `INFO` | `DEBUG` while you work |

A healthy start logs `events_faults ready. in=sensor_data out=fault_events`.
The processor reads from the *latest* offset, so it sits silent until
something new is published. See the hints below for how to feed it.

## What you fill in

Everything to write is in `main.py`, marked `TODO 1`–`3` inside the poll loop.
The setup code around them already works.

1. **Decode**: `record.value` into an `OrionSensorData`.
   [`car_status/main.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/telemetry/stack/processors/car_status/main.py)
   does this in `_car_from_headers` and `_decode`.
2. **Detect**: work out which faults are active in this frame. For now that
   means `diagnostics_high`; later it means the SDD-generated signals (see
   above).
3. **Diff and publish**: compare with the previous frame's active set, and
   send one `fault_raised` / `fault_cleared` event to `OUTPUT_TOPIC` per
   change. `car_status`'s `_emit` shows a `KafkaProducer` with a JSON
   serializer.

Also expected:

- **A test.** Put detect and diff in pure functions in their own file, with no
  Kafka in them, and add a `py_test` in `BUILD.bazel`. `car_status/` has the
  pattern: `classifier.py`, `test_classifier.py`, and their `BUILD.bazel`
  entries.
- **A README update.** Once it works, replace the task list in README.md with
  what the processor actually does, and list the fault signals it consumes.

Keep the Kafka plumbing out of the test. If the decision logic needs a broker
to test, it is in the wrong place.

## Hints for getting unstuck

**See what's on a topic.** Use the console consumer that ships in the Kafka
container. No install needed:

```bash
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:29092 --topic fault_events --from-beginning
```

`sensor_data` is binary protobuf, so reading it that way prints garbage. To
check that frames are arriving at all, add `--property print.headers=true` and
look for `car_type:Orion`. To list every topic, run
`docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:29092 --list`.

**No car, no CAN hardware?** You don't need either. Faults are rare on a
healthy car anyway, so a fake is the only way to see raised *and* cleared
events on demand.

- *Unit test first.* Feed your detect/diff functions hand-built sequences:
  no faults, then one raised, then the same frame again (expect no event),
  then cleared. Run
  `bazel test --config=local //apps/telemetry/stack/processors/events_faults/...`. That
  covers most of the logic without Docker.
- *Fake frames on the real pipeline.* Publish your own `OrionSensorData` to
  `sensor_data` from the host with the same header ingest adds. Run from
  `apps/telemetry/` so `stack.ingest.protobuf` imports:

  ```bash
  cd apps/telemetry && uv run --no-project --with kafka-python --with 'protobuf>=7.35' python -c "
  from kafka import KafkaProducer
  from stack.ingest.protobuf import can_packets_pb2
  f = can_packets_pb2.OrionSensorData(packet_id=1)
  f.diagnostics_high.stomp_fault = True
  KafkaProducer(bootstrap_servers='localhost:29092').send(
      'sensor_data', f.SerializeToString(), headers=[('car_type', b'Orion')]).get(10)"
  ```

  Send it again with the fault cleared, and you should see one raised and one
  cleared event on `fault_events`.
  [`stack/tests/test_data_flow.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/telemetry/stack/tests/test_data_flow.py)
  does the same thing at a larger scale.

**Import errors on `can_packets_pb2`, or a field that isn't there?** The
checked-in Python bindings can lag the schema. That will be the first thing
you hit when the SDD fault signals land. Regenerate them from the
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

## End-to-end: where do these signals come from

This is an exercise. The rest of this guide starts at `sensor_data`, and this
section covers the half before it: how a CAN signal is defined and becomes a
field on `OrionSensorData`. The SDD fault signals will take the same path.
You add a throwaway signal, regenerate, watch it show up in the Python
bindings, and then revert everything. Nothing here touches `main.py`, and
none of it gets committed.

```
can_packets.csv ──update_can_proto──▶ can_packets.proto ──protoc──▶ can_packets_pb2.py ──▶ OrionSensorData
  (you edit this)                     (checked in, generated)       (Python bindings)       (what main.py decodes)
```

The proto is generated but still checked in because protobuf field numbers
are permanent. Once data has been recorded with field 39 meaning X, 39 can
never mean anything else. The generator reads the numbers from the existing
proto so they never move. The [BEVO schema README](../../../../BEVO/schema/README.md)
explains this, and it is why step 5 matters.

**0. Start clean.** `git status apps/BEVO/schema` should show nothing, so the
revert at the end is one command.

**1. Define the signal.** Open
[`apps/BEVO/schema/can_packets.csv`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/BEVO/schema/can_packets.csv)
in a text editor. Don't use Excel or Sheets: they re-quote every cell and
rewrite the whole file. Find the `0x1C1` row (`Accelerator Pedal`). Replace
its first `unused` cell (`Data[3]`) with:

```
"Practice Fault (uint8, bool); practice_fault (bool)"
```

Then bump that row's DLC (the 7th column) from `3` to `4`, because the frame
now carries one more byte. Each cell reads `Human name (CAN type, scale);
proto_name (proto type)`. The part after `;` is what becomes a proto field.

**2. Generate the proto.** From the repo root:

```bash
bazel run --config=local //apps/BEVO/schema:update_can_proto
git diff apps/BEVO/schema
```

It should report `annotated 1 CSV cell(s)` with no `Warning:` lines above it.
The diff shows two things:

- In the CSV, your cell gained a `#N` suffix. That is its field number,
  written back so it stays fixed.
- In the proto, `message DiagnosticsHigh` gained `bool practice_fault = N;`.
  N is `39` at the time of writing. It will be higher if fields have been
  added since.

Skipping this step is a common real mistake. The CSV and proto disagree, and
the car silently drops the new signal.
`bazel test --config=local //apps/BEVO/schema:proto_current_test` catches it,
and it runs in CI.

Why `DiagnosticsHigh`? The generator sorts fields into sub-messages by name
and packet rate. See `_partition_for_field` in
[`generate_can_proto.py`](https://github.com/LonghornRacingElectric/lhre/blob/main/apps/BEVO/schema/generate_can_proto.py).
Names containing `fault` go to diagnostics, and packets at 50 Hz or faster
go to the *High* half (`0x1C1` runs at 333 Hz). From the code, predict where
it would land if the row's frequency were `10`. Don't try that for real. It
would also move the row's existing APPS fault bits, and the generator refuses
with `Duplicate proto id ... in message DiagnosticsLow` rather than renumber
them. That refusal is the permanent-field-number rule doing its job.

**If step 2 doesn't go as described.** The generator is forgiving, and a
mistake usually drops your signal instead of failing:

- **A `Warning:` line** names the row and cell it skipped. The usual causes
  are a missing `;`, a proto name with spaces or dashes (use `snake_case`), or
  a proto type other than `float` or `bool`.
- **Empty proto diff, no warning:** the name already exists. Reusing a proto
  name merges into that field instead of adding one. Check with
  `grep -w your_name apps/BEVO/schema/can_packets.proto`.
- **`Error: this would change the type of existing proto field(s)`:** same
  cause, with a different type. Pick a new name.
- **`Error: Duplicate proto id`:** you typed a `#N` yourself. Delete it; the
  generator assigns numbers.
- **The field landed in `Dynamics`:** names containing `accel`, `gps`,
  `steer`, `wheel_speed`, ... route there before `fault` is checked.
- **Only replace a cell that says `unused`.** A blank cell after a `uint16`
  is that signal's second byte. A signal written there quietly shifts every
  later signal in the row, and nothing warns you.
- **Keep the double quotes around the cell.** It contains a comma, and
  without the quotes the row's columns shift.

The [BEVO schema README](../../../../BEVO/schema/README.md#cell-format-and-gotchas)
has the full list. To check the car side too,
`bazel build --config=local //apps/BEVO:sensor_proto` compiles BEVO's decoder
against your proto. The first build takes a few minutes.

**3. Compile the Python bindings and find your field.**

```bash
bazel build --config=local //apps/telemetry/stack/ingest:can_packets_pb2
cd bazel-bin/apps/telemetry/stack/ingest/protobuf
uv run --no-project --with 'protobuf>=7.35' python -c "
import can_packets_pb2 as p
f = p.OrionSensorData()
f.diagnostics_high.practice_fault = True
print(f)"
```

This prints a `diagnostics_high` block containing `practice_fault: true`. That is the object
`main.py` gets from TODO 1's decode step, so a real signal added this way is
readable as `frame.diagnostics_high.<name>`. Now run
`grep -c practice_fault` on the checked-in
`apps/telemetry/stack/ingest/protobuf/can_packets_pb2.py`. It prints `0`,
which is the "bindings lag the schema" problem from the hints, reproduced on
purpose.

**4. Think about what didn't change.** The CAN frame and the proto changed,
but no firmware sends `practice_fault`, so on the real car it would stay
`false` forever. Defining a signal and producing it are separate jobs. The
producing side is firmware in `boards/`, not this pipeline.

**5. Revert.** From the repo root:

```bash
git checkout -- apps/BEVO/schema/can_packets.csv apps/BEVO/schema/can_packets.proto
git status apps/BEVO/schema   # clean again
```

`bazel-bin` regenerates on the next build, so it needs no cleanup. Never
commit a practice signal: it would take its field number permanently.

**Beyond the exercise:**

- The database half of SDD (SQL, Prisma and ORM models, via
  `scripts/sync_schema.sh`; see [SDD Usage](../../../scripts/docs/SDD_USAGE.md))
  is not needed for a Kafka processor, so skip it here.
- The CSV is today's source of truth. The CAN spec in
  [lib/spec](../../../../../lib/spec/README.md) is set to replace it
  ([ADR-008](../../../../../docs/architecture/008-can-spec-pipeline.md)). The
  commands will change, but the path from definition to generated field to
  `OrionSensorData` stays the same.
