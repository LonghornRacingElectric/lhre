"""Bounded Kafka batches; failed work remains in Kafka, never in a second queue."""
import logging
import os
import signal
import time

from kafka import KafkaConsumer, TopicPartition
from kafka.consumer.subscription_state import ConsumerRebalanceListener
from kafka.structs import OffsetAndMetadata

from analysis.sql_utils.db_session import engine_angelique, engine_nightwatch, engine_orion
from stack.processors.db_writer.writer import DatabaseWriter


def positive(name, default, cast=int):
    value = cast(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def consume(consumer, writer, stopping, max_packets=500, max_bytes=8 * 1024 * 1024, interval=0.25, assignment=None):
    pending = []
    byte_count = 0
    deadline = None

    def flush():
        nonlocal pending, byte_count, deadline
        writer.write(pending)
        offsets = {}
        for record in pending:
            partition = TopicPartition(record.topic, record.partition)
            offsets[partition] = OffsetAndMetadata(record.offset + 1, "", -1)
        # Explicit offsets: never acknowledge a fetched record that has not been written.
        consumer.commit(offsets=offsets)
        pending = []
        byte_count = 0
        deadline = None

    try:
        while not stopping():
            timeout = 1000 if deadline is None else max(0, min(1000, int((deadline - time.monotonic()) * 1000)))
            batch = consumer.poll(timeout_ms=timeout, max_records=max_packets)
            if assignment is not None:
                assignment.check()
            for records in batch.values():
                for record in records:
                    if pending and (len(pending) >= max_packets or byte_count + len(record.value) > max_bytes
                                    or time.monotonic() >= deadline):
                        flush()
                    if len(record.value) > max_bytes:
                        raise ValueError("sensor_data record exceeds DB_BATCH_BYTES")
                    if not pending:
                        deadline = time.monotonic() + interval
                    pending.append(record)
                    byte_count += len(record.value)
            if pending and (len(pending) >= max_packets or time.monotonic() >= deadline):
                flush()
        if pending:
            flush()
    finally:
        # A failed transaction or lost group membership must replay on restart.
        consumer.close(autocommit=False)


class InitialOffsets(ConsumerRebalanceListener):
    def __init__(self, consumer):
        self.consumer = consumer
        self.error = None

    def check(self):
        if self.error is not None:
            raise self.error

    def on_partitions_revoked(self, partitions):
        pass

    def on_partitions_assigned(self, partitions):
        try:
            self._assign(partitions)
        except Exception as error:
            # kafka-python logs and swallows listener exceptions. Stop fetching
            # and surface this explicitly in the main consume loop instead.
            self.error = error
            self.consumer.pause(*partitions)

    def _assign(self, partitions):
        starts = self.consumer.beginning_offsets(partitions)
        for partition in partitions:
            offset = self.consumer.committed(partition)
            if offset is None:
                self.consumer.seek(partition, starts[partition])
                logging.warning("New writer group starts at retained offset %s/%s", partition, starts[partition])
            elif offset < starts[partition]:
                raise RuntimeError(f"Kafka retention gap: {partition} committed={offset} oldest={starts[partition]}")
            else:
                self.consumer.seek(partition, offset)


def main():
    logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
    logging.getLogger("kafka").setLevel(logging.WARNING)
    stopped = False

    def stop(*_args):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    group = os.getenv("KAFKA_GROUP_ID", "telemetry-db-writer-v1")
    writer = DatabaseWriter({"Nightwatch": engine_nightwatch, "Angelique": engine_angelique,
                             "Orion": engine_orion}, identity=group)
    max_packets = positive("DB_BATCH_PACKETS", "500")
    max_bytes = positive("DB_BATCH_BYTES", str(8 * 1024 * 1024))
    interval = positive("DB_BATCH_SECONDS", "0.25", float)
    # Retry by recreating the consumer, so any failed batch is fetched again from
    # committed offsets. This also avoids polling past a failed partition.
    while not stopped:
        try:
            writer.initialize()
            consumer = KafkaConsumer(
                bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092").split(","),
                group_id=group, enable_auto_commit=False, auto_offset_reset="none",
                max_poll_records=max_packets, max_poll_interval_ms=300000,
                max_partition_fetch_bytes=max_bytes,
            )
            assignment = InitialOffsets(consumer)
            consumer.subscribe([os.getenv("KAFKA_INPUT_TOPIC", "sensor_data")], listener=assignment)
            logging.info("Consuming sensor_data as %s", group)
            consume(consumer, writer, lambda: stopped, max_packets, max_bytes, interval, assignment)
        except Exception:
            logging.exception("Database writer stopped batch; offsets retained for retry")
            for _ in range(50):
                if stopped:
                    break
                time.sleep(0.1)


if __name__ == "__main__":
    main()
