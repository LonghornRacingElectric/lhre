"""Batch raw Kafka telemetry into packet and sensor tables."""
from collections import defaultdict
import logging
import os
import signal
import threading
import time

from kafka import KafkaConsumer
from sqlalchemy.dialects.postgresql import insert

from analysis.sql_utils.db_session import get_db
from analysis.sql_utils.query_builder import QueryBuilder
from stack.ingest.partition_manager import PartitionManager
from stack.ingest.sensor_decode import CAR_MESSAGES, car_from_headers, decode_sensor, sensor_rows


def write_batch(records, models, specs):
    packets = defaultdict(dict)
    for record in records:
        car = car_from_headers(record.headers)
        rows = sensor_rows(decode_sensor(record.value, car), models[car])
        packets[car].setdefault(rows["packet"]["packet_id"], rows)
    for car, rows_by_id in packets.items():
        with get_db(car) as session, session.begin():
            packet = models[car]["packet"].__table__
            statement = insert(packet).values([rows["packet"] for rows in rows_by_id.values()])
            # A database commit can succeed before its Kafka offset commit. Only
            # insert child rows for newly admitted packets when that batch replays.
            admitted = session.execute(statement.on_conflict_do_nothing(
                index_elements=[packet.c.packet_id]).returning(packet.c.packet_id)).scalars().all()
            jobs = [(table, row) for packet_id in admitted
                    for table, row in rows_by_id[packet_id].items() if table != "packet"]
            QueryBuilder.execute_insert(session, jobs, specs[car], car, commit=False)
            if car in {"Orion", "Angelique"} and admitted:
                PartitionManager().record_batch(session, [int(rows_by_id[key]["packet"]["time"]) for key in admitted])


def main():
    logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
    logging.getLogger("kafka").setLevel(logging.WARNING)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    models = {car: {model.__tablename__: model for model in QueryBuilder._model_classes_for_car(car)}
              for car in CAR_MESSAGES}
    specs = {car: QueryBuilder(car).get_table_column_specs() for car in CAR_MESSAGES}
    size = int(os.getenv("DB_BATCH_PACKETS", "500"))
    interval = float(os.getenv("DB_BATCH_SECONDS", "0.25"))
    if size <= 0 or interval <= 0:
        raise ValueError("Batch size and interval must be positive")
    while not stopped.is_set():
        consumer = None
        try:
            consumer = KafkaConsumer(
                os.getenv("KAFKA_INPUT_TOPIC", "sensor_data"),
                bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092").split(","),
                group_id=os.getenv("KAFKA_GROUP_ID", "telemetry-db-writer-v1"),
                enable_auto_commit=False, auto_offset_reset="earliest", max_poll_records=size,
            )
            logging.info("Consuming sensor_data for database persistence")
            while not stopped.is_set():
                records = []
                deadline = time.monotonic() + interval
                while len(records) < size and not stopped.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    batch = consumer.poll(timeout_ms=max(1, int(remaining * 1000)), max_records=size - len(records))
                    records.extend(record for values in batch.values() for record in values)
                if records:
                    write_batch(records, models, specs)
                    consumer.commit()
        except Exception:
            logging.exception("Database insert failed; uncommitted Kafka records will retry")
            stopped.wait(5)
        finally:
            if consumer is not None:
                consumer.close(autocommit=False)


if __name__ == "__main__":
    main()
