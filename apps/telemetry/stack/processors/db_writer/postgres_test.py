"""Isolated databases only: POSTGRES_TEST_URL must name a disposable test server."""
import os
import unittest
import uuid
import time
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from analysis.sql_utils.models import Partitions
from stack.ingest.sensor_decode import CAR_MESSAGES, SENSOR_TABLES
from stack.processors.db_writer.writer import CHECKPOINTS, DatabaseWriter


class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["POSTGRES_TEST_URL"])
        cls.admin = create_engine(url, isolation_level="AUTOCOMMIT")
        cls.engines = {}
        cls.names = []
        for car in CAR_MESSAGES:
            name = "writer_test_" + uuid.uuid4().hex
            with cls.admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}"'))
            cls.names.append(name)
            cls.engines[car] = create_engine(url.set(database=name))
        cls.writer = DatabaseWriter(cls.engines)
        cls.writer.initialize()
        for car, engine in cls.engines.items():
            for table in SENSOR_TABLES:
                if table in cls.writer.models[car]:
                    cls.writer.models[car][table].__table__.create(engine)
            Partitions.__table__.create(engine)
            # Deployed generated sensor tables have no child primary keys. Exercise
            # that layout too: replay safety must not depend on the ORM declaration.
            with engine.begin() as connection:
                for table in SENSOR_TABLES:
                    if table != "packet" and table in cls.writer.models[car]:
                        connection.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{table}_pkey"'))

    @classmethod
    def tearDownClass(cls):
        for engine in cls.engines.values():
            engine.dispose()
        with cls.admin.connect() as connection:
            for name in cls.names:
                connection.execute(text(f'DROP DATABASE "{name}"'))
        cls.admin.dispose()

    def setUp(self):
        for car, engine in self.engines.items():
            tables = [table for table in SENSOR_TABLES if table in self.writer.models[car]]
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE " + ", ".join(tables) + ", partitions, sensor_ingest_checkpoint CASCADE"))

    def record(self, car="Nightwatch", offset=0, packet_id=1, time=1000000):
        message = CAR_MESSAGES[car](time=time, packet_id=packet_id)
        message.dynamics.SetInParent()
        return SimpleNamespace(topic="sensor_data", partition=0, offset=offset,
                               headers=[("car_type", car.encode())], value=message.SerializeToString())

    def count(self, car, table):
        with self.engines[car].connect() as connection:
            return connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()

    def test_batch_parent_first_and_replay_for_all_cars(self):
        records = [self.record(car, offset=i, packet_id=1) for i, car in enumerate(CAR_MESSAGES)]
        self.writer.write(records)
        self.writer.write(records)  # DB commit succeeded, Kafka commit was lost.
        self.writer.write([self.record(car, offset=i + 3, packet_id=1) for i, car in enumerate(CAR_MESSAGES)])
        for car in CAR_MESSAGES:
            self.assertEqual(self.count(car, "packet"), 1)
            self.assertEqual(self.count(car, "dynamics"), 1)

    def test_child_failure_rolls_back_parent_and_checkpoint_then_retries(self):
        engine = self.engines["Nightwatch"]
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE dynamics ADD CONSTRAINT forced_failure CHECK (packet_id <> 2)"))
        try:
            with self.assertRaises(Exception):
                self.writer.write([self.record(packet_id=1), self.record(packet_id=2, offset=1)])
            self.assertEqual(self.count("Nightwatch", "packet"), 0)
            self.assertEqual(self.count("Nightwatch", "sensor_ingest_checkpoint"), 0)
        finally:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE dynamics DROP CONSTRAINT forced_failure"))
        self.writer.write([self.record(packet_id=1), self.record(packet_id=2, offset=1)])
        self.assertEqual(self.count("Nightwatch", "dynamics"), 2)

    def test_partial_mixed_car_commit_is_replayable(self):
        original = self.writer._write_car
        def fail_second(car, items):
            if car == "Orion":
                raise RuntimeError("second database disconnected")
            original(car, items)
        records = [self.record(), self.record("Orion", offset=1)]
        with patch.object(self.writer, "_write_car", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.writer.write(records)
        self.writer.write(records)
        for car in ("Nightwatch", "Orion"):
            self.assertEqual(self.count(car, "dynamics"), 1)

    def test_reused_packet_id_with_different_time_blocks_checkpoint(self):
        self.writer.write([self.record()])
        with self.assertRaises(ValueError):
            self.writer.write([self.record(offset=1, time=2000000)])
        with self.engines["Nightwatch"].connect() as connection:
            self.assertEqual(connection.execute(select(CHECKPOINTS.c.next_offset)).scalar_one(), 1)

    @unittest.skipUnless(os.getenv("KAFKA_TEST_BROKERS"), "requires a disposable Kafka broker")
    def test_real_kafka_backlog_replays_after_database_commit(self):
        from kafka import KafkaConsumer, KafkaProducer, TopicPartition
        from kafka.admin import KafkaAdminClient, NewTopic
        from stack.processors.db_writer.main import consume, InitialOffsets
        brokers = os.environ["KAFKA_TEST_BROKERS"].split(",")
        topic = "writer-test-" + uuid.uuid4().hex
        group = "writer-test-" + uuid.uuid4().hex
        admin = KafkaAdminClient(bootstrap_servers=brokers)
        admin.create_topics([NewTopic(topic, 1, 1)])
        producer = KafkaProducer(bootstrap_servers=brokers)
        try:
            for car in ("Nightwatch", "Orion"):
                event = self.record(car)
                producer.send(topic, value=event.value, headers=event.headers).get(timeout=10)
            # The database has no rows until its independent consumer runs.
            self.assertEqual(self.count("Nightwatch", "packet"), 0)
            def new_consumer():
                consumer = KafkaConsumer(bootstrap_servers=brokers, group_id=group,
                                         enable_auto_commit=False, auto_offset_reset="none")
                consumer.subscribe([topic], listener=InitialOffsets(consumer))
                return consumer
            consumer = new_consumer()
            deadline = time.monotonic() + 20
            with patch.object(consumer, "commit", side_effect=RuntimeError("crash before Kafka commit")):
                with self.assertRaisesRegex(RuntimeError, "crash before Kafka commit"):
                    consume(consumer, self.writer, lambda: time.monotonic() > deadline, max_packets=2)
            self.assertEqual(self.count("Nightwatch", "dynamics"), 1)
            self.assertEqual(self.count("Orion", "dynamics"), 1)
            consumer = new_consumer()
            partition = TopicPartition(topic, 0)
            deadline = time.monotonic() + 20
            reached_end = [False]
            def stop():
                reached_end[0] = consumer.committed(partition) == 2
                return reached_end[0] or time.monotonic() > deadline
            consume(consumer, self.writer, stop, max_packets=2)
            self.assertTrue(reached_end[0], "replayed Kafka offsets never committed")
            for car in ("Nightwatch", "Orion"):
                self.assertEqual(self.count(car, "dynamics"), 1)
        finally:
            producer.close()
            admin.delete_topics([topic])
            admin.close()

    def test_large_batch_chunks_statements_and_preserves_arrays(self):
        records = []
        for i in range(1200):
            message = CAR_MESSAGES["Nightwatch"](time=1000000 + i, packet_id=i + 1)
            message.dynamics.cent_mass_accel.extend([1.5, 0.0, -2.0])
            event = self.record(offset=i, packet_id=i + 1)
            event.value = message.SerializeToString()
            records.append(event)
        self.writer.write(records)
        self.assertEqual(self.count("Nightwatch", "dynamics"), 1200)
        with self.engines["Nightwatch"].connect() as connection:
            row = connection.execute(text("SELECT cent_mass_accel FROM dynamics WHERE packet_id = 1200")).scalar_one()
            self.assertEqual(row, [1.5, 0.0, -2.0])

    def test_session_gaps_and_low_rate_batches(self):
        self.writer.write([self.record("Orion", offset=i, packet_id=i + 1, time=stamp)
                           for i, stamp in enumerate((1000000, 1000100, 1400000, 1400100, 1800000))])
        self.assertEqual(self.count("Orion", "partitions"), 3)


if __name__ == "__main__":
    unittest.main()
