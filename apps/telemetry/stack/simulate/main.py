"""simulate: publishes fake Orion telemetry frames to Kafka's ``sensor_data``.

Every other processor in this stack (car_status, board_health, events_faults,
gg_plot, ...) consumes ``sensor_data``. Without a real car or a recorded replay,
there is nothing for them to react to. This fills that gap for local dev: it
manufactures plausible-looking `OrionSensorData` frames, occasionally injects a
board dropout or a fault so `board_health`/`events_faults` have something to
report, and publishes them at a steady rate.

It is dev tooling, not a source of truth: values are randomized within roughly
plausible ranges, not physically simulated. See README.md for what's covered
and what isn't. It writes to the SAME ``sensor_data`` topic real hardware uses
via `ingest` — don't run it at the same time as a real car or a replay.
"""

import logging
import os
import random
import signal
import time

from kafka import KafkaProducer
from stack.ingest.protobuf import can_packets_pb2

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logging.getLogger("kafka").setLevel(logging.WARNING)

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
OUTPUT_TOPIC = os.getenv("KAFKA_OUTPUT_TOPIC", "sensor_data")
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


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    producer = KafkaProducer(bootstrap_servers=BOOTSTRAP)
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
