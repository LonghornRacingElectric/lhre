# Simulate processor

Optional dev tool: publishes Orion telemetry frames to Kafka's `sensor_data`
topic so the other processors (`car_status`, `board_health`, `events_faults`,
`gg_plot`, ...) have something to react to without a real car or a live rig.

By default it **replays real car data**: 110 s of an autocross run at UTA on
2026-10-17, the stack's reference sample. Every processor sees the same
frames, so a result is comparable across machines and people.

## What it does

**Replay (default).** Loads `data/autox_uta_2026-10-17.pb.gz` and publishes its
frames in order, spaced by their recorded frame times (about 20 Hz), then
loops. Each frame gets a fresh `packet_id` (1, 2, 3, ...) and `time` (now, in
ms), so consumers that age data against the wall clock treat it as live. Every
frame goes out with a `car_type: Orion` header (the header every processor
keys off of). Everything else is exactly what the car sent.

**Random (`SIM_DATA=random`).** The older generator. Every ~`1/SIM_RATE_HZ`
seconds it builds one frame with randomized-but-plausible dynamics, controls,
pack, and thermal values. It also drives two small state machines, so the
fault/health processors have transitions to catch, which the AutoX sample
doesn't have:

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
./apps/telemetry/stack/server_devtool.sh enable simulate        # build + start; replays the AutoX sample
./apps/telemetry/stack/server_devtool.sh logs simulate           # "simulate replaying ...: 2172 frames over 110 s"
./apps/telemetry/stack/server_devtool.sh stop simulate           # or: stop everything with `stop`
```

Without Docker, against the core stack's host listener:

```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:29092 bazel run //apps/telemetry/stack/simulate:simulate_binary
```

Point any other optional processor at it the normal way, e.g.
`enable board_health` or `enable car_status`, and watch their logs — they'll
start reacting within a few seconds.

**Don't run this at the same time as a real car, rig, or replay.** It writes
to the same `sensor_data` topic `ingest` does; running both mixes real and fake
frames with no way to tell them apart downstream.

## The AutoX sample

`data/autox_uta_2026-10-17.pb.gz` (352 KB; 2.8 MB unpacked):

- **What:** run 54 at the UTA autocross, 2026-10-17 21:23:56–21:25:47 UTC.
  The window takes in 10 s before the run starts and the stop after it. It
  holds 2172 frames, with no gap longer than 61 ms, and GPS in every frame. Over
  the run, `accel_pedal_travel` covers 0–1, `controls.motor_speed` peaks at 4369,
  `torque_feedback` at 212, `dynamics.gps_speed` at 51.9, and
  `steer_col_angle` swings ±131.
  `pack.hv_c` is stuck near 588 on this car, so it isn't a current reading.
- **From:** the server's `sensor_data` topic. These are the bytes `ingest`
  forwarded from the car, checked against the `orion` database for the same
  `packet_id` range.
- **Changed:** fields the repo's `OrionSensorData` doesn't define were
  dropped: the car's newer `dynamics` field 36. Otherwise a future field 36
  would misread it. Frames carry only car signals and GPS (the public UTA
  lot), with no names or credentials.
- **Format:** gzip of length-prefixed frames, each a little-endian `uint32`
  byte count followed by one serialized `OrionSensorData`. Point `SIM_DATA` at
  another file in the same format to replay it instead.
- **Not in it:** board dropouts or fault transitions. Use `SIM_DATA=random` to
  exercise those paths in `board_health`/`events_faults`.

## Configuration

Set in `docker-compose.yml`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Broker address inside `telemetry_network` |
| `KAFKA_OUTPUT_TOPIC` | `sensor_data` | Where frames are published |
| `SIM_DATA` | the AutoX sample | Recording to replay, or `random` for the generator |
| `SIM_RATE_HZ` | `5` | Random mode only: frames per second |
| `SIM_DROPOUT_P` | `0.05` | Random mode only: per-tick, per-board chance of missing a refresh |
| `SIM_FAULT_TOGGLE_P` | `0.01` | Random mode only: per-tick chance of flipping a fault flag |
| `SIM_SEED` | unset | Random mode only: set for a reproducible sequence |

## Limits

- **Orion only.** No Angelique/Nightwatch frames.
- **One 110 s run, looped.** At each loop the car jumps from the end of the
  run back to the start line; lap and track logic will see that as a teleport.
- **Random mode is randomized, not physical.** Values are drawn from plausible
  ranges (e.g. pack voltage 400-550V), not a real vehicle model — don't use it
  for dashboards that assume physically-consistent signals across fields.
- **Only two fault flags and one bitmask bit pair** in random mode. Enough to
  exercise `events_faults`, not a full fault catalog.

## Files

`main.py` is the replayer, generator, and Kafka producer. `data/` holds the
AutoX sample, packaged into the binary's runfiles (and so the image) by
`BUILD.bazel`. Container targets follow the standard `simulate_binary`,
`_image`, `_load`, `_push`, and `_smoke_test` contract. It is optional and
never part of `core_images`.
