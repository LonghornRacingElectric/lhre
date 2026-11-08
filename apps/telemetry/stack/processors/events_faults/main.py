"""Publish raised/cleared Orion fault events from telemetry snapshots."""

import json
import logging
import os
import signal
from threading import Event

from google.protobuf.message import DecodeError
from kafka import KafkaConsumer, KafkaProducer
from stack.ingest.protobuf import can_packets_pb2

from faults import detect_active_faults, diff_faults


def process_record(record, producer, active_faults, output_topic):
    """Decode one Orion frame and publish changes from its previous fault set."""
    try:
        car = None
        for key, value in record.headers or []:
            key = key.decode("utf-8") if isinstance(key, bytes) else key
            if key.lower() == "car_type":
                car = value.decode("utf-8") if isinstance(value, bytes) else value
                break
        if car is None or car.strip().lower() != "orion":
            return []

        frame = can_packets_pb2.OrionSensorData()
        frame.ParseFromString(record.value)
        if not frame.HasField("diagnostics_high"):
            return []
        current = detect_active_faults(frame.diagnostics_high)
    except (DecodeError, ValueError, TypeError, AttributeError) as error:
        logging.warning("Skipping invalid telemetry: %s", error)
        return []

    car = "orion"
    events = diff_faults(active_faults.get(car, set()), current, car, frame.time)
    for event in events:
        producer.send(output_topic, value=event).get(timeout=10)
        logging.info("%s %s %s", car, event["kind"], event["fault"])
    active_faults[car] = current
    return events


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
        enable_auto_commit=False,
        auto_offset_reset="latest",
    )
    producer = None
    try:
        producer = KafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda value: json.dumps(value).encode("utf-8"),
            acks="all",
        )
        active_faults = {}
        logging.info("events_faults ready. in=%s out=%s", input_topic, output_topic)
        while not stop.is_set():
            batch = consumer.poll(timeout_ms=1000)
            for records in batch.values():
                for record in records:
                    process_record(record, producer, active_faults, output_topic)
            if batch:
                consumer.commit()
    finally:
        try:
            if producer is not None:
                producer.close(timeout=10)
        finally:
            consumer.close(autocommit=False)


if __name__ == "__main__":
    main()
