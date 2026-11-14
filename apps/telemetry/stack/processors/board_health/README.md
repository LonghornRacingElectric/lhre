# Board health processor

The project extends the existing last-seen dashboard with per-board health-packet
data once "Per-board health packet" lands. Last-seen establishes recent
communication; health packets report the board's health. A board can be live
while reporting a fault.

Currently, the processor decodes Artemis frames from `sensor_data`, tracks communication
state independently for the boards declared by the generated schema, publishes one JSON event to
`board_health` per state change, and publishes a snapshot of every board's state to
`board_health_state`. Start with [GETTING_STARTED.md](GETTING_STARTED.md).

## Input and states

Only frames with a `car_type: Artemis` header are decoded. Frames from other cars
and untagged frames are skipped and do not count as a live feed. The board list comes from the Bazel-generated `ArtemisSensorData` descriptor:
`board_status.<board>_last_seen_s` fields define the tracked boards. Input uses
`TelemetryBatch` with `payload_format: artemis_updates_v1`.

| State | Rule |
| --- | --- |
| `UNKNOWN` | Missing/invalid board status or value, or telemetry timeout. |
| `NEVER_SEEN` | `last_seen_s == -1`: not heard from since the CAN reader started. |
| `LIVE` | `0 <= last_seen_s < stale_after_s`. Communication does not prove healthy operation. |
| `STALE` | `last_seen_s >= stale_after_s`, assuming the board is expected to communicate. |

`last_seen_s` is elapsed seconds since communication, or `-1` for never seen.
`stale_after_s` is the allowed silence, configured by `BOARD_STALE_AFTER_S`.
The default, 0.5 s, is about 4.5 times the slowest healthy gap in the UTA
autocross recording (DUI, 0.111 s; the other boards stay under 0.021 s). It is
one value for every board until per-board thresholds are generated from the spec.
Negative values other than `-1`, booleans, nonnumeric values, NaN, and infinity
are invalid. Only fields in an update's presence mask are observations, including zero.
Absent fields and malformed batches do not refresh a board's arrival timeout.

States start unknown. First live/stale/never-seen observations and later changes
are logged and published once; unchanged states are quiet. A reset to `-1` is
never seen, not recovery.

The poll loop checks for silence even when no messages arrive, using a monotonic
clock. After `BOARD_TELEMETRY_TIMEOUT_S` without an observation for a board, that
board becomes unknown. This measures local arrival freshness only: delayed/replayed
frames are not rejected, and a valid-looking old frame can change state. Source
freshness needs a timestamp/session policy before production use.

Defaults are experimental simulation settings, not validated production timings.
All monitored boards are assumed expected to send.
Threshold fluctuations currently cause immediate transitions; no debounce is
applied.

## Output events

One JSON message per board per state change, keyed by board name so a board's
events stay ordered if the topic ever has more than one partition:

```json
{"car": "artemis", "board": "vcu", "state": "STALE", "previous": "LIVE",
 "last_seen_s": 9.0, "cause": "frame", "t_ms": 1767225600000, "t_source": "car"}
```

| Field | Meaning |
| --- | --- |
| `car` | Always `artemis`: only Artemis frames are processed (see above). |
| `state`, `previous` | States from the table above. |
| `last_seen_s` | The value that caused the change; `null` for timeouts, missing blocks, and invalid values. |
| `cause` | `frame` (a decoded frame changed it) or `telemetry_timeout` (no observation for that board for `BOARD_TELEMETRY_TIMEOUT_S`). |
| `t_ms` | Event time, epoch ms. See `t_source`. |
| `t_source` | `car`: the frame's `time` field, set by `cand` when it timestamped the observation. `server`: this processor's clock, used for timeout events and for frames whose car time looks unsynced. |

Frame events use the car's time so late or replayed frames keep the time they
happened. The car's CM5 sets its clock over the network, so a car that boots
without a network or an RTC backup battery can report a wrong time until it
syncs. A frame time before 2025-01-01 or more than 5 minutes ahead of the
server clock is treated as unsynced, and the event carries server time with
`t_source: server`. A wrong but plausible car time cannot be detected here.

Delivery is at least once. Each event is sent with `acks="all"` and the
processor waits for Kafka to acknowledge it; input offsets are committed only
after every event from a poll is acknowledged. A failed send stops the
processor, and on restart it reprocesses the uncommitted frames, so an event
can repeat but is never silently dropped.

The first observation after startup publishes `UNKNOWN -> ...` for that board.
Change events are not re-sent while states hold; the snapshot topic below
covers consumers that start later.

## State snapshots

`board_health_state` carries every board's current state in one message:

```json
{"car": "artemis",
 "boards": {"dui": "LIVE", "hvc": "LIVE", "inverter": "STALE", "vcu": "LIVE"},
 "t_ms": 1767225600000, "t_source": "car"}
```

A snapshot is published after any poll in which a board changed, and every
`BOARD_SNAPSHOT_INTERVAL_S` otherwise, including before the first frame (all
`UNKNOWN`). At most one goes out per poll. `t_ms` and `t_source` come from the
newest update in that poll, or from the server clock when none arrived. The
gateway's `/v1/topics` feeds keep only a topic's latest message, so each snapshot
is complete on its own: a client that connects late or misses one still sees
every board. Use `board_health` for the history of transitions and
`board_health_state` for the current picture.

Timeout events come from the poll loop's silence check, not from a frame, so
they are the only signal that the whole feed died. A per-board `UNKNOWN` with
`cause: frame` instead means that board's observed value was invalid. Malformed batches are skipped; boards still expire normally. After a restart the tracker has seen no frames, so a feed that is
already dead produces no timeout event; consumers keep their last state.

## Future work

Health-packet interpretation and dashboard integration are the next part of the
project once the per-board packets are available. Keep reported health separate
from communication state so a live board's faults remain visible.

`startup_grace_s` is the proposed allowance before a board's first communication,
measured from when it becomes expected to send. It is not implemented.
`WAITING`, `NOT_EXPECTED`, and startup-timeout detection need reliable
startup/power signals. Until those exist, `-1` remains `NEVER_SEEN`.
Native updates use the shared Artemis decoder.

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

## Tests

`classifier.py` holds the decisions, the event builder (`change_events`) and the
snapshot builder and timer (`snapshot_event`, `SnapshotTimer`) without Kafka.
`test_classifier.py` covers threshold boundaries, invalid/missing data, initial
stale state, repeated silence, recovery, reader reset, independent/simultaneous
boards, telemetry timeout, and event shape (including JSON-safe nulls for
NaN/infinity).

Snapshots are tested end to end in `events_faults`' Kafka integration test, which
runs the real `board_health` and `events_faults` images against a Kafka container
and real Artemis batches. It checks that every snapshot names every board, that
the heartbeat repeats unchanged states, that a feed timeout reaches the snapshot,
and that a board going quiet shows `STALE` with the car's timestamp. It needs
Docker and loaded images:

```bash
bazel test --config=local //apps/telemetry/stack/processors/events_faults:kafka_integration_test
``` Run without Docker:

```bash
bazel test --config=local //apps/telemetry/stack/processors/board_health:classifier_test
```

## Configuration

Set in `docker-compose.yml`. Timeout values must be finite and positive.
Override the timeout environment variables when starting the service.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address inside `telemetry_network` |
| `KAFKA_INPUT_TOPIC` | `sensor_data` | Raw protobuf frames |
| `KAFKA_OUTPUT_TOPIC` | `board_health` | Per-board change events |
| `KAFKA_STATE_TOPIC` | `board_health_state` | Snapshots of every board's state |
| `KAFKA_GROUP_ID` | `board-health-group` | Consumer group |
| `BOARD_STALE_AFTER_S` | `0.5` | `stale_after_s` threshold, seconds |
| `BOARD_TELEMETRY_TIMEOUT_S` | `10` | Experimental arrival timeout, seconds |
| `BOARD_SNAPSHOT_INTERVAL_S` | `2` | Heartbeat between snapshots when nothing changes, seconds |
| `LOGLEVEL` | `INFO` | Use `DEBUG` for decoded frames |

## Files

`main.py` the poll loop and producer; `classifier.py` the state tracker, event builder and snapshot builder.
The `:classifier` library and `:classifier_test` target isolate the decisions. `Dockerfile` / `docker-compose.yml` the legacy Docker
path. `BUILD.bazel` the Bazel path: targets follow the standard
`board_health_binary`, `_image`, `_load`, `_push`, and `_smoke_test` contract. It
is optional and never part of `core_images`.

The Bazel image packages its generated Artemis schema, CAN and storage metadata
and is labeled as a schema consumer for deployment agreement checks. See the
[stack deployment guide](../../README.md).
