"""Board communication-state processor.

Decodes Orion sensor_data, tracks per-board communication states, and publishes
one JSON event to board_health per state change. Health-packet interpretation is
future work. See README.md.
"""

import json
import logging
import os
import signal
import time

from kafka import KafkaConsumer, KafkaProducer
from stack.ingest.protobuf import can_packets_pb2
from google.protobuf.json_format import MessageToDict
from google.protobuf.message import DecodeError

from classifier import BoardStateTracker, change_events

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logging.getLogger("kafka").setLevel(logging.WARNING)

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
INPUT_TOPIC = os.getenv("KAFKA_INPUT_TOPIC", "sensor_data")
OUTPUT_TOPIC = os.getenv("KAFKA_OUTPUT_TOPIC", "board_health")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "board-health-group")

# Experimental settings for sim data
STALE_AFTER_S = float(os.getenv("BOARD_STALE_AFTER_S", "5"))
TELEMETRY_TIMEOUT_S = float(os.getenv("BOARD_TELEMETRY_TIMEOUT_S", "10"))

stop = False


def _request_stop(signum, _frame):
    # Docker sends SIGTERM on `stop`; without a handler PID 1 ignores it and the
    # container is killed after the timeout.
    global stop
    stop = True

def _decode_sensor_payload(payload: bytes) -> dict:
    row = can_packets_pb2.OrionSensorData()
    row.ParseFromString(payload)
    return MessageToDict(
        row,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )


def _publish(producer, changes, board_status, cause):
    # Wall clock, not the tracker's monotonic time: consumers need a real timestamp.
    for event in change_events(changes, board_status, cause, int(time.time() * 1000)):
        logging.info("board=%s state=%s -> %s (%s)",
                     event["board"], event["previous"], event["state"], cause)
        # Keyed by board so one board's events stay ordered on a multi-partition topic.
        producer.send(OUTPUT_TOPIC, key=event["board"].encode(), value=event)


def main():
    tracker = BoardStateTracker(STALE_AFTER_S, TELEMETRY_TIMEOUT_S)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=BOOTSTRAP,
        group_id=GROUP_ID,
        auto_offset_reset="latest",
    )
    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    logging.info("board_health ready. in=%s out=%s", INPUT_TOPIC, OUTPUT_TOPIC)

    seen = 0
    try:
        while not stop:
            for records in consumer.poll(timeout_ms=1000).values():
                for record in records:
                    seen += 1
                    if seen % 100 == 1:  # sensor_data is fast; don't log every frame
                        logging.info("frame #%d: %d bytes, headers=%s", seen, len(record.value), record.headers)

                    # TODO 1: decode record.value into an OrionSensorData (car_status/main.py shows how).
                    try:
                        decoded_message = _decode_sensor_payload(payload=record.value)
                    except DecodeError as error:
                        logging.warning("Invalid sensor payload: %s", error)
                        decoded_message = {}
                    logging.debug("Decoded Message: %s", decoded_message)

                    # TODO 2: read its board_status and decide which boards are stale.
                    board_status = decoded_message.get("board_status")
                    changes = tracker.observe(board_status, time.monotonic())

                    # TODO 3: when a board changes state, publish an event to OUTPUT_TOPIC.
                    _publish(producer, changes, board_status, "frame")

            # A dead feed produces no records, so this is the only place a car
            # going silent is noticed. It runs after every poll, empty or not.
            _publish(producer, tracker.expire(time.monotonic()), None, "telemetry_timeout")
    finally:
        # Timeout events are often the last thing sent before a stop; don't drop them.
        producer.flush()
        producer.close()
        consumer.close()


if __name__ == "__main__":
    main()
