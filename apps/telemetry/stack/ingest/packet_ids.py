"""Control-plane ID lookup, including packets not yet persisted by db_writer."""
import os
import time
from kafka import KafkaConsumer, TopicPartition
from stack.ingest.sensor_decode import car_from_headers, decode_sensor


def retained_packet_high_water(car, minimum=0):
    consumer = KafkaConsumer(
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092" if os.getenv("IN_DOCKER") else "localhost:29092").split(","),
        enable_auto_commit=False, group_id=None, auto_offset_reset="none",
    )
    try:
        topic = "sensor_data"
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            raise RuntimeError("sensor_data metadata unavailable; cannot safely allocate packet ID")
        assigned = [TopicPartition(topic, partition) for partition in partitions]
        consumer.assign(assigned)
        starts = consumer.beginning_offsets(assigned)
        ends = consumer.end_offsets(assigned)
        for partition in assigned:
            consumer.seek(partition, starts[partition])
        deadline = time.monotonic() + 120
        while any(consumer.position(partition) < ends[partition] for partition in assigned):
            if time.monotonic() >= deadline:
                raise TimeoutError("Kafka packet ID scan timed out")
            for partition, records in consumer.poll(timeout_ms=1000).items():
                for record in records:
                    if record.offset < ends[partition] and car_from_headers(record.headers) == car:
                        minimum = max(minimum, int(decode_sensor(record.value, car)["packet_id"]))
        return minimum
    finally:
        consumer.close(autocommit=False)
