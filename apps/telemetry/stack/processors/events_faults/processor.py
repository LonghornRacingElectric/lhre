"""Decode telemetry and publish acknowledged fault transitions."""

import logging

from google.protobuf.message import DecodeError
from stack.ingest.protobuf import can_packets_pb2

from faults import detect_active_faults, diff_faults


def car_from_headers(headers):
    """Require an explicit, unambiguous car tag before decoding protobuf."""
    cars = []
    for key, value in headers or []:
        key = key.decode("utf-8") if isinstance(key, bytes) else key
        if key.lower() == "car_type":
            value = value.decode("utf-8") if isinstance(value, bytes) else value
            cars.append(value.strip().lower())
    return cars[0] if len(cars) == 1 else None


class FaultProcessor:
    def __init__(self, producer, output_topic="fault_events"):
        self.producer = producer
        self.output_topic = output_topic
        self._states = {}

    def process(self, record):
        """Publish transitions, remembering state only after all acknowledgments."""
        try:
            car = car_from_headers(record.headers)
            if car != "orion":
                logging.debug("Skipping unsupported or untagged car: %s", car)
                return []
            frame = can_packets_pb2.OrionSensorData()
            frame.ParseFromString(record.value)
            if frame.time <= 0 or not frame.HasField("diagnostics_high"):
                raise ValueError("Missing positive telemetry time or diagnostics_high")
            current = detect_active_faults(frame.diagnostics_high)
        except (DecodeError, ValueError, TypeError, AttributeError) as error:
            logging.warning("Skipping invalid telemetry at %s:%s:%s: %s",
                            record.topic, record.partition, record.offset, error)
            return []

        previous_time, previous = self._states.get(car, (0, set()))
        if frame.time < previous_time:
            logging.warning("Skipping older telemetry for %s: %s < %s",
                            car, frame.time, previous_time)
            return []

        events = diff_faults(previous, current, car, frame.time)
        for event in events:
            event["packet_id"] = frame.packet_id
            # Kafka coordinates stay the same on replay, unlike a random UUID.
            event["event_id"] = (
                f"{record.topic}:{record.partition}:{record.offset}:"
                f"{event['kind']}:{event['fault']}"
            )
            self.producer.send(
                self.output_topic, value=event, key=car.encode("utf-8"),
            ).get(timeout=10)
            logging.info("%s %s %s at %s", car, event["kind"], event["fault"], frame.time)

        self._states[car] = (frame.time, current)
        return events

    def process_batch(self, consumer, batch):
        for records in batch.values():
            for record in records:
                self.process(record)
        if batch:
            # Publish failures propagate out of process(), so this cannot
            # advance offsets past an event whose send was not acknowledged.
            consumer.commit()
