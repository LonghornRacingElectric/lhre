"""simulate: publishes Orion telemetry frames to Kafka's ``sensor_data``.

Every other processor in this stack (car_status, board_health, events_faults,
gg_plot, ...) consumes ``sensor_data``. Without a real car there is nothing for
them to react to. This fills that gap for local dev.

By default it replays real car data: 110 s of an autocross run at UTA on
2026-10-17 (``data/``), looped, paced by the recorded frame times, restamped to
now. ``SIM_DATA=random`` switches to the older generator, which manufactures
plausible-looking frames and injects board dropouts and faults so
`board_health`/`events_faults` have transitions to report.

It writes to the SAME ``sensor_data`` topic real hardware uses via `ingest`.
Don't run it at the same time as a real car or a replay. See README.md.
"""

import gzip
import logging
import os
import random
import signal
import struct
import time

from kafka import KafkaProducer
from stack.ingest.protobuf import can_packets_pb2

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logging.getLogger("kafka").setLevel(logging.WARNING)

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
OUTPUT_TOPIC = os.getenv("KAFKA_OUTPUT_TOPIC", "sensor_data")
# A recording (gzip of uint32-LE length-prefixed OrionSensorData), or "random".
SIM_DATA = os.getenv("SIM_DATA") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "autox_uta_2026-10-17.pb.gz"
)
RATE_HZ = float(os.getenv("SIM_RATE_HZ", "5"))
SEED = os.getenv("SIM_SEED")
# Per tick, chance a board "misses" its refresh (last_seen_s keeps climbing)
# and chance a fault flag flips. Independent per board/fault.
DROPOUT_P = float(os.getenv("SIM_DROPOUT_P", "0.05"))
FAULT_TOGGLE_P = float(os.getenv("SIM_FAULT_TOGGLE_P", "0.01"))

BOARDS = ("csm", "dui", "hvc", "inverter", "pdu", "tsm", "usm", "vcu")
# A handful of diagnostics_high fault signals worth flipping. run_faults bits
# are picked arbitrarily; there's no public bit-to-name table (see
# events_faults/README.md), so this doesn't claim to be a real inverter fault.
FAULT_FLAGS = ("stomp_fault", "apps1_disconnect")

rng = random.Random(int(SEED)) if SEED else random.Random()
stop = False


def _request_stop(signum, _frame):
    global stop
    stop = True
    logging.info("signal %s: stopping simulate", signum)


def _build_frame(packet_id, last_seen, run_faults_bit, active_flags):
    frame = can_packets_pb2.OrionSensorData()
    frame.packet_id = packet_id
    frame.time = int(time.time() * 1000)

    frame.dynamics.steer_col_angle = rng.uniform(-2.5, 2.5)
    frame.dynamics.fl_wheel_speed = frame.dynamics.fr_wheel_speed = rng.uniform(0, 100)
    frame.dynamics.accel_pedal_travel = rng.uniform(0, 1)

    frame.controls.apps1_travel = frame.controls.apps1_v = frame.dynamics.accel_pedal_travel
    frame.controls.torque_request = rng.uniform(0, 230)

    frame.pack.hv_soc = max(0.0, 90.0 - (packet_id % 36000) / 400.0)  # slow drain, then wraps
    frame.pack.hv_pack_v = rng.uniform(400, 550)
    frame.pack.lv_batt_v = rng.uniform(12.5, 13.8)

    frame.thermal.coolant_temp = rng.uniform(20, 45)
    frame.thermal.cell_top_temp = frame.thermal.cell_bottom_temp = rng.uniform(20, 40)

    frame.diagnostics_high.run_faults = float(1 << run_faults_bit) if run_faults_bit is not None else 0.0
    for flag in active_flags:
        setattr(frame.diagnostics_high, flag, True)

    for board in BOARDS:
        setattr(frame.board_status, f"{board}_last_seen_s", last_seen[board])

    return frame.SerializeToString()


def _load_recording(path):
    """Returns [(seconds to wait before sending, frame)], in recorded order."""
    with gzip.open(path, "rb") as f:
        blob = f.read()
    frames, i = [], 0
    while i < len(blob):
        (n,) = struct.unpack_from("<I", blob, i)
        frames.append(can_packets_pb2.OrionSensorData.FromString(blob[i + 4 : i + 4 + n]))
        i += 4 + n
    if not frames:
        raise SystemExit(f"simulate: no frames in {path}")
    gaps = [(b.time - a.time) / 1000.0 for a, b in zip(frames, frames[1:])]
    # The loop seam gets the average gap so the wrap doesn't stall or burst.
    seam = sum(gaps) / len(gaps) if gaps else 1.0 / RATE_HZ
    return list(zip([seam] + gaps, frames))


def _replay(producer, recording):
    packet_id = 0
    while not stop:
        for gap_s, frame in recording:
            if stop:
                return
            time.sleep(max(gap_s, 0.0))
            # Restamped so consumers that age data against wall-clock see it as live.
            packet_id += 1
            frame.packet_id = packet_id
            frame.time = int(time.time() * 1000)
            producer.send(OUTPUT_TOPIC, value=frame.SerializeToString(), headers=[("car_type", b"Orion")])
        logging.info("replay: sent %d frames, looping", packet_id)


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    producer = KafkaProducer(bootstrap_servers=BOOTSTRAP)
    if SIM_DATA != "random":
        recording = _load_recording(SIM_DATA)
        span_s = sum(gap for gap, _ in recording[1:])
        logging.info("simulate replaying %s: %d frames over %.0f s, out=%s", SIM_DATA, len(recording), span_s, OUTPUT_TOPIC)
        _replay(producer, recording)
        producer.flush()
        return
    logging.info(
        "simulate ready. out=%s rate=%sHz dropout_p=%s fault_toggle_p=%s",
        OUTPUT_TOPIC, RATE_HZ, DROPOUT_P, FAULT_TOGGLE_P,
    )

    last_seen = {board: 0.0 for board in BOARDS}
    active_flags = set()
    run_faults_bit = None
    period_s = 1.0 / RATE_HZ
    packet_id = 0

    while not stop:
        for board in BOARDS:
            # Refresh (reset to ~0) unless this tick "misses" the board's CAN frame.
            last_seen[board] = 0.0 if rng.random() > DROPOUT_P else last_seen[board] + period_s

        if rng.random() < FAULT_TOGGLE_P:
            flag = rng.choice(FAULT_FLAGS)
            active_flags ^= {flag}  # toggle: add if absent, remove if present
        if rng.random() < FAULT_TOGGLE_P:
            run_faults_bit = None if run_faults_bit is not None else rng.choice((0, 2))

        packet_id += 1
        payload = _build_frame(packet_id, last_seen, run_faults_bit, active_flags)
        producer.send(OUTPUT_TOPIC, value=payload, headers=[("car_type", b"Orion")])
        time.sleep(period_s)

    producer.flush()


if __name__ == "__main__":
    main()
