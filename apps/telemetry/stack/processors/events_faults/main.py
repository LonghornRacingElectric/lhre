"""events_faults processor.

Kafka plumbing only: connects, reads ``sensor_data``, and logs what arrives. The
actual events-faults logic is the exercise; see README.md.
"""

import logging
import os
import signal

from kafka import KafkaConsumer

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logging.getLogger("kafka").setLevel(logging.WARNING)

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
INPUT_TOPIC = os.getenv("KAFKA_INPUT_TOPIC", "sensor_data")
OUTPUT_TOPIC = os.getenv("KAFKA_OUTPUT_TOPIC", "fault_events")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "events-faults-group")

stop = False

# These boolean fields are explicit fault signals. Fuse/contactor/status bits
# are intentionally omitted: their asserted polarity is not documented here.
_BOOLEAN_FAULTS = (
    "apps1_disconnect",
    "apps2_disconnect",
    "apps1_out_range",
    "apps2_out_range",
    "apps_mismatch",
    "apps_implause",
    "bpps1_disconnect",
    "bpps2_disconnect",
    "bpps1_out_range",
    "bpps2_out_range",
    "bpps_mismatch",
    "bse1_disconnect",
    "bse2_disconnect",
    "bse1_out_range",
    "bse2_out_range",
    "stomp_fault",
)


def detect_active_faults(diagnostics_high):
    """Return stable names for asserted fault signals in a diagnostics block.

    The inverter fields are float32 bitmasks. Only bits 0–23 are decoded:
    binary32 cannot preserve every integer bit above that range. Their per-bit
    meanings are not available in this repo, so retain the source and bit index.
    """
    active = set()
    if diagnostics_high is None:
        return active

    for field in _BOOLEAN_FAULTS:
        if getattr(diagnostics_high, field, False):
            active.add(field)

    for field in ("post_faults", "run_faults"):
        value = getattr(diagnostics_high, field, 0.0)
        try:
            mask = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if mask < 0 or mask != value:
            continue
        for bit in range(24):
            if mask & (1 << bit):
                active.add(f"{field}[{bit}]")
    return active


def _request_stop(signum, _frame):
    # Docker sends SIGTERM on `stop`; without a handler PID 1 ignores it and the
    # container is killed after the timeout.
    global stop
    stop = True


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=BOOTSTRAP,
        group_id=GROUP_ID,
        auto_offset_reset="latest",
    )
    logging.info("events_faults ready. in=%s out=%s", INPUT_TOPIC, OUTPUT_TOPIC)

    seen = 0
    while not stop:
        for records in consumer.poll(timeout_ms=1000).values():
            for record in records:
                seen += 1
                if seen % 100 == 1:  # sensor_data is fast; don't log every frame
                    logging.info("frame #%d: %d bytes, headers=%s", seen, len(record.value), record.headers)

                # TODO 1: decode record.value into an OrionSensorData (car_status/main.py shows how).
                # TODO 2: read its diagnostics_high and work out which faults are active.
                # TODO 3: compared with the previous frame, publish a raised/cleared event to
                #         OUTPUT_TOPIC (car_status/main.py's _emit shows a KafkaProducer).
    consumer.close()


if __name__ == "__main__":
    main()
