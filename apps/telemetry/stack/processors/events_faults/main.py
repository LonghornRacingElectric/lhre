"""Publish Orion fault transitions from sensor_data to fault_events."""

import json
import logging
import os
import signal
from threading import Event

from kafka import KafkaConsumer, KafkaProducer

from processor import FaultProcessor


def main():
    logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
    logging.getLogger("kafka").setLevel(logging.WARNING)
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    input_topic = os.getenv("KAFKA_INPUT_TOPIC", "sensor_data")
    output_topic = os.getenv("KAFKA_OUTPUT_TOPIC", "fault_events")
    group_id = os.getenv("KAFKA_GROUP_ID", "events-faults-group")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    consumer = KafkaConsumer(
        input_topic,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        max_poll_records=20,
        enable_auto_commit=False,
        auto_offset_reset="latest",
    )
    producer = None
    try:
        producer = KafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
            acks="all",
            retries=3,
            max_in_flight_requests_per_connection=1,
        )
        processor = FaultProcessor(producer, output_topic)
        logging.info("events_faults ready. in=%s out=%s", input_topic, output_topic)
        while not stop.is_set():
            processor.process_batch(consumer, consumer.poll(timeout_ms=1000))
    finally:
        try:
            if producer is not None:
                producer.close(timeout=10)
        finally:
            consumer.close(autocommit=False)


if __name__ == "__main__":
    main()
