# Board health processor

The project extends the existing last-seen dashboard with per-board health-packet
data once "Per-board health packet" lands. Last-seen establishes recent
communication; health packets report the board's health. A board can be live
while reporting a fault.

Currently, the processor decodes Orion frames from `sensor_data`, tracks communication
state independently for eight boards, and publishes one JSON event to
`board_health` per state change. Start with [GETTING_STARTED.md](GETTING_STARTED.md).

## Input and states

Input is assumed to be Orion protobuf, without header filtering. A shared topic
containing other car schemas needs a `car_type` filter before decoding. The
`board_status` block contains `<board>_last_seen_s` for `csm`, `dui`, `hvc`,
`inverter`, `pdu`, `tsm`, `usm`, and `vcu`.

| State | Rule |
| --- | --- |
| `UNKNOWN` | Missing/invalid board status or value, or telemetry timeout. |
| `NEVER_SEEN` | `last_seen_s == -1`: not heard from since the CAN reader started. |
| `LIVE` | `0 <= last_seen_s < stale_after_s`. Communication does not prove healthy operation. |
| `STALE` | `last_seen_s >= stale_after_s`, assuming the board is expected to communicate. |

`last_seen_s` is elapsed seconds since communication, or `-1` for never seen.
`stale_after_s` is the allowed silence, configured by `BOARD_STALE_AFTER_S`.
Negative values other than `-1`, booleans, nonnumeric values, NaN, and infinity
are invalid. An absent block is unknown; individual unset protobuf scalar
fields can decode to zero and cannot be distinguished from explicit zeroes.
Malformed protobuf frames mark all boards unknown without stopping the loop.

States start unknown. First live/stale/never-seen observations and later changes
are logged and published once; unchanged states are quiet. A reset to `-1` is
never seen, not recovery.

The poll loop checks for silence even when no messages arrive, using a monotonic
clock. After `BOARD_TELEMETRY_TIMEOUT_S` without a processed frame, all boards
become unknown. This measures local arrival freshness only: delayed/replayed
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
{"car": "orion", "board": "vcu", "state": "STALE", "previous": "LIVE",
 "last_seen_s": 9.0, "cause": "frame", "t_ms": 1767225600000}
```

| Field | Meaning |
| --- | --- |
| `car` | Always `orion`: input is assumed to be Orion (see above). |
| `state`, `previous` | States from the table above. |
| `last_seen_s` | The value that caused the change; `null` for timeouts, missing blocks, and invalid values. |
| `cause` | `frame` (a decoded frame changed it) or `telemetry_timeout` (no frames for `BOARD_TELEMETRY_TIMEOUT_S`). |
| `t_ms` | Processor wall-clock time at publish, epoch ms. Not the car's time. |

The first frame after startup publishes `UNKNOWN -> ...` for every board; that
is the policy, so a consumer gets an initial state without a separate snapshot.
Nothing is re-sent while states hold, so a consumer that starts later sees
nothing until the next change. A heartbeat like `car_status`'s would fix that.

Timeout events come from the poll loop's silence check, not from a frame, so
they are the only signal that the whole feed died. A per-board `UNKNOWN` with
`cause: frame` instead means that board's value was missing or invalid. One
malformed frame flips every board to `UNKNOWN` and back on the next good frame
(16 events). After a restart the tracker has seen no frames, so a feed that is
already dead produces no timeout event; consumers keep their last state.

## Future work

Health-packet interpretation and dashboard integration are the next part of the
project once the per-board packets are available. Keep reported health separate
from communication state so a live board's faults remain visible.

`startup_grace_s` is the proposed allowance before a board's first communication,
measured from when it becomes expected to send. It is not implemented.
`WAITING`, `NOT_EXPECTED`, and startup-timeout detection need reliable
startup/power signals. Until those exist, `-1` remains `NEVER_SEEN`.
Shared decoding, normalization, and header parsing also remain to be consolidated.

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

`classifier.py` holds the decisions and the event builder (`change_events`)
without Kafka. `test_classifier.py` covers threshold boundaries, invalid/missing
data, initial stale state, repeated silence, recovery, reader reset,
independent/simultaneous boards, telemetry timeout, and event shape (including
JSON-safe nulls for NaN/infinity). Run without Docker:

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
| `KAFKA_OUTPUT_TOPIC` | `board_health` | Where your events go |
| `KAFKA_GROUP_ID` | `board-health-group` | Consumer group |
| `BOARD_STALE_AFTER_S` | `5` | Experimental `stale_after_s` threshold, seconds |
| `BOARD_TELEMETRY_TIMEOUT_S` | `10` | Experimental arrival timeout, seconds |
| `LOGLEVEL` | `INFO` | Use `DEBUG` for decoded frames |

## Files

`main.py` the poll loop and producer; `classifier.py` the state tracker and event builder.
The `:classifier` library and `:classifier_test` target isolate the decisions. `Dockerfile` / `docker-compose.yml` the legacy Docker
path. `BUILD.bazel` the Bazel path: targets follow the standard
`board_health_binary`, `_image`, `_load`, `_push`, and `_smoke_test` contract. It
is optional and never part of `core_images`.
