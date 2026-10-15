# Simulate processor

Optional dev tool: publishes fake Orion telemetry frames to Kafka's
`sensor_data` topic so the other processors (`car_status`, `board_health`,
`events_faults`, `gg_plot`, ...) have something to react to without a real car,
a live rig, or a recorded replay.

## What it does

Every ~`1/SIM_RATE_HZ` seconds it builds one `OrionSensorData` protobuf frame
with randomized-but-plausible dynamics, controls, pack, and thermal values, and
publishes it with a `car_type: Orion` header (the header every processor keys
off of). It also drives two small state machines so the fault/health
processors have real transitions to catch, not just noise:

- **Board dropouts.** Each of the 8 boards' `last_seen_s` normally resets to 0
  every tick; with probability `SIM_DROPOUT_P` it instead keeps climbing,
  eventually crossing `board_health`'s stale threshold.
- **Fault flips.** With probability `SIM_FAULT_TOGGLE_P`, `stomp_fault` or
  `apps1_disconnect` toggles, or a bit in `run_faults` toggles — giving
  `events_faults` a raised/cleared pair to emit.

## Quick start

Needs the core stack (Kafka) running first. From the repo root:

```bash
./apps/telemetry/stack/server_devtool.sh up                    # core stack, if not already up
./apps/telemetry/stack/server_devtool.sh enable simulate        # build + start the simulator
./apps/telemetry/stack/server_devtool.sh logs simulate           # watch it publish
./apps/telemetry/stack/server_devtool.sh stop simulate           # or: stop everything with `stop`
```

Point any other optional processor at it the normal way, e.g.
`enable board_health` or `enable car_status`, and watch their logs — they'll
start reacting to the fake frames within a few seconds.

**Don't run this at the same time as a real car, rig, or replay.** It writes
to the same `sensor_data` topic `ingest` does; running both mixes real and fake
frames with no way to tell them apart downstream.

## Configuration

Set in `docker-compose.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address inside `telemetry_network` |
| `KAFKA_OUTPUT_TOPIC` | `sensor_data` | Where frames are published |
| `SIM_RATE_HZ` | `5` | Frames per second |
| `SIM_DROPOUT_P` | `0.05` | Per-tick, per-board chance of missing a refresh |
| `SIM_FAULT_TOGGLE_P` | `0.01` | Per-tick chance of flipping a fault flag |
| `SIM_SEED` | unset | Set for a reproducible sequence |

## Limits

- **Orion only.** No Angelique/Nightwatch frames.
- **Randomized, not physical.** Values are drawn from plausible ranges (e.g.
  pack voltage 400-550V), not a real vehicle model — don't use this for load
  testing dashboards that assume physically-consistent signals across fields.
- **Only two fault flags and one bitmask bit pair.** Enough to exercise
  `events_faults`, not a full fault catalog.

## Files

`main.py` the generator and Kafka producer. Container targets follow the
standard `simulate_binary`, `_image`, `_load`, `_push`, and `_smoke_test`
contract. It is optional and never part of `core_images`.
