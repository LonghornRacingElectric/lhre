"""Publish raised/cleared Artemis fault events from native updates."""

import json
import logging
import os
import signal
from threading import Event

from google.protobuf.message import DecodeError
from kafka import KafkaConsumer, KafkaProducer
from stack.ingest.sensor_decode import car_from_headers, decode_updates, payload_format

from faults import detect_active_faults, diff_faults


def process_record(record, producer, diagnostics, output_topic):
    """Apply observed fault fields after their transition events are acknowledged."""
    try:
        car = car_from_headers(record.headers)
        if car != "Artemis":
            return []
        payload_format(record.headers, car)
        updates = decode_updates(record.value)
    except (DecodeError, ValueError, TypeError) as error:
        logging.warning("Skipping invalid telemetry: %s", error)
        return []

    events = []
    for update in updates:
        observed = update.get("diagnostics_high")
        if not observed:
            continue
        try:
            previous = detect_active_faults(diagnostics)
            current = detect_active_faults(diagnostics | observed)
        except ValueError as error:
            logging.warning("Skipping invalid diagnostics: %s", error)
            continue
        changes = diff_faults(previous, current, "artemis", update["time"])
        for event in changes:
            producer.send(output_topic, value=event).get(timeout=10)
            logging.info("artemis %s %s", event["kind"], event["fault"])
        diagnostics.update(observed)
        events.extend(changes)
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
        diagnostics = {}
        logging.info("events_faults ready. in=%s out=%s", input_topic, output_topic)
        while not stop.is_set():
            batch = consumer.poll(timeout_ms=1000)
            for records in batch.values():
                for record in records:
                    process_record(record, producer, diagnostics, output_topic)
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
