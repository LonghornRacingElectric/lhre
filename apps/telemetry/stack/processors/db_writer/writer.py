"""Atomic, replayable batches of raw telemetry. Kafka offsets are next offsets."""
from collections import defaultdict
import logging

from sqlalchemy import BigInteger, Column, Integer, MetaData, String, Table, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from analysis.sql_utils.query_builder import QueryBuilder
from stack.ingest.partition_manager import PartitionManager
from stack.ingest.sensor_decode import car_from_headers, decode_sensor, sensor_rows

CHECKPOINTS = Table(
    "sensor_ingest_checkpoint", MetaData(),
    Column("consumer", String, primary_key=True),
    Column("topic", String, primary_key=True),
    Column("partition", Integer, primary_key=True),
    Column("next_offset", BigInteger, nullable=False),
    schema="public",
)


class DatabaseWriter:
    def __init__(self, engines, identity="telemetry-db-writer-v1"):
        self.engines = engines
        self.identity = identity
        self.models = {car: {model.__tablename__: model for model in
                            QueryBuilder._model_classes_for_car(car)} for car in engines}
        self.specs = {car: QueryBuilder(car).get_table_column_specs() for car in engines}

    def initialize(self):
        for engine in self.engines.values():
            CHECKPOINTS.create(engine, checkfirst=True)

    def write(self, records):
        # Validate the entire batch before any database work. Unknown schemas block progress.
        by_car = defaultdict(list)
        for record in records:
            car = car_from_headers(record.headers)
            by_car[car].append((record, decode_sensor(record.value, car)))
        for car, items in by_car.items():
            self._write_car(car, items)

    def _write_car(self, car, items):
        from sqlalchemy.orm import Session
        engine = self.engines[car]
        models = self.models[car]
        with Session(engine) as session, session.begin():
            session.execute(text("SET LOCAL synchronous_commit = on"))
            session.execute(text("SET LOCAL statement_timeout = 30000"))
            session.execute(text("SET LOCAL lock_timeout = 10000"))
            # Serializes writers for session metadata and duplicate admission, including
            # an old group member still finishing a transaction after a rebalance.
            session.execute(text("SELECT pg_advisory_xact_lock(721685491)"))
            offsets = {}
            for record, _ in items:
                key = (record.topic, record.partition)
                if key not in offsets:
                    offsets[key] = session.execute(select(CHECKPOINTS.c.next_offset).where(
                        CHECKPOINTS.c.consumer == self.identity,
                        CHECKPOINTS.c.topic == key[0], CHECKPOINTS.c.partition == key[1],
                    )).scalar_one_or_none() or 0
            packets = {}
            next_offsets = dict(offsets)
            for record, message in items:
                key = (record.topic, record.partition)
                if record.offset < offsets[key]:
                    continue
                rows = sensor_rows(message, models)
                packet_id = rows["packet"]["packet_id"]
                if packet_id in packets and packets[packet_id] != rows:
                    raise ValueError(f"Conflicting payloads for {car} packet {packet_id}")
                packets[packet_id] = rows
                next_offsets[key] = max(next_offsets[key], record.offset + 1)

            if packets:
                packet_table = models["packet"].__table__
                admitted = set()
                packet_rows = [packets[key]["packet"] for key in sorted(packets)]
                for chunk in chunks(packet_rows, 1000):
                    stmt = pg_insert(packet_table).values(chunk).on_conflict_do_nothing(
                        index_elements=[packet_table.c.packet_id]).returning(packet_table.c.packet_id)
                    admitted.update(session.execute(stmt).scalars())
                # Existing complete packets belong to the old writer/logsync or an earlier
                # Kafka delivery. First writer wins; never append duplicate child rows.
                existing = set(packets) - admitted
                if existing:
                    for chunk in chunks(sorted(existing), 1000):
                        saved = dict(session.execute(select(packet_table.c.packet_id, packet_table.c.time)
                                                     .where(packet_table.c.packet_id.in_(chunk))).all())
                        for packet_id in chunk:
                            if saved[packet_id] != int(packets[packet_id]["packet"]["time"]):
                                raise ValueError(f"Packet ID reused with different time: {car}/{packet_id}")
                jobs = defaultdict(list)
                times = []
                for packet_id in sorted(admitted):
                    rows = packets[packet_id]
                    times.append(int(rows["packet"]["time"]))
                    for table, row in rows.items():
                        if table != "packet":
                            jobs[table].append(QueryBuilder.get_insert_values(
                                table, row, models[table], self.specs[car][table]))
                for table, rows in jobs.items():
                    # Limit bound parameters even for wide diagnostics messages.
                    keys = set().union(*rows)
                    normalized = [{key: row.get(key) for key in keys} for row in rows]
                    for chunk in chunks(normalized, max(1, min(1000, 30000 // len(keys)))):
                        session.execute(insert(models[table].__table__).values(chunk))
                if car in {"Orion", "Angelique"} and times:
                    PartitionManager().record_batch(session, times)
                logging.info("Persisting %s: %d new packets, %d duplicates", car, len(admitted), len(existing))
            for (topic, partition), offset in next_offsets.items():
                stmt = pg_insert(CHECKPOINTS).values(consumer=self.identity, topic=topic,
                                                     partition=partition, next_offset=offset)
                session.execute(stmt.on_conflict_do_update(
                    index_elements=[CHECKPOINTS.c.consumer, CHECKPOINTS.c.topic, CHECKPOINTS.c.partition],
                    set_={"next_offset": offset}))


def chunks(rows, size):
    for start in range(0, len(rows), size):
        yield rows[start:start + size]
