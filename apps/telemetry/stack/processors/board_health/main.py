"""board_health processor skeleton.

Kafka plumbing only: connects, reads ``sensor_data``, and logs what arrives. The
actual board-health logic is the exercise; see README.md.
"""

import logging
import os
import signal

from kafka import KafkaConsumer

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logging.getLogger("kafka").setLevel(logging.WARNING)

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
INPUT_TOPIC = os.getenv("KAFKA_INPUT_TOPIC", "sensor_data")
OUTPUT_TOPIC = os.getenv("KAFKA_OUTPUT_TOPIC", "board_health")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "board-health-group")

stop = False


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
    logging.info("board_health ready. in=%s out=%s", INPUT_TOPIC, OUTPUT_TOPIC)

    seen = 0
    while not stop:
        for records in consumer.poll(timeout_ms=1000).values():
            for record in records:
                seen += 1
                if seen % 100 == 1:  # sensor_data is fast; don't log every frame
                    logging.info("frame #%d: %d bytes, headers=%s", seen, len(record.value), record.headers)

                # TODO 1: decode record.value into an OrionSensorData (car_status/main.py shows how).
                # TODO 2: read its board_status and decide which boards are stale.
                # TODO 3: when a board changes state, publish an event to OUTPUT_TOPIC
                #         (car_status/main.py's _emit shows a KafkaProducer).
    consumer.close()


if __name__ == "__main__":
    main()
