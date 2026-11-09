"""Database behavior tests, against an explicitly supplied disposable database."""

import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
import os
import unittest

from sqlalchemy import create_engine, select, func, inspect
from sqlalchemy.orm import Session
from apps.telemetry.scripts.apply_storage import changes
from lib.codegen.telemetry import storage
from lib.codegen.telemetry.artemis_models import POOLS

from analysis.sql_utils.models import ARTEMIS_MODELS, ArtemisRecordingSession, ArtemisTransportState
from stack.ingest.pool_storage import write_snapshots, write_gps, POOL_MODELS


@unittest.skipUnless(os.getenv("POOL_TEST_DATABASE_URL"), "Needs an isolated Timescale test database")
class PoolStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(os.environ["POOL_TEST_DATABASE_URL"])

    def setUp(self):
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.session = Session(self.connection)

    def tearDown(self):
        self.session.close()
        self.transaction.rollback()
        self.connection.close()

    def message(self, stamp, value=12.0):
        return {"time": stamp, "packet_id": 123, "controls": {"apps1_travel": value, "motor_speed": 900.0},
                "dynamics": {"gps_precise": [30.123456789, -97.123456789], "fl_sprung_accel": [0.0, 1.0, 2.0]},
                "pack": {"cells_v": [3.5, 3.6]}}

    def test_snapshots_keep_raw_times_and_bucket_without_multiplication(self):
        write_snapshots(self.session, [self.message(1_000_010, 10), self.message(1_000_020, 20)])
        raw = POOL_MODELS["controls_333hz"]
        self.assertEqual(self.session.scalars(select(raw.time).order_by(raw.time)).all(), [1_000_010, 1_000_020])
        controls, dynamics = ARTEMIS_MODELS["ArtemisControls"], ARTEMIS_MODELS["ArtemisDynamics"]
        result = self.session.execute(select(controls.apps1_travel, dynamics.gps_precise).join(
            dynamics, (controls.time == dynamics.time) & (controls.session_start_ms == dynamics.session_start_ms))).all()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].apps1_travel, 15.0)
        peaks = func.controls_buckets(100, 1_000_000, 1_001_000, "max").table_valued("apps1_travel")
        self.assertEqual(self.session.scalar(select(peaks.c.apps1_travel)), 20.0)
        self.assertEqual(result[0].gps_precise, [30.123456789, -97.123456789])

    def test_replay_is_idempotent_and_conflicts_rollback(self):
        message = self.message(2_000_010)
        write_snapshots(self.session, [message, copy.deepcopy(message)])
        write_snapshots(self.session, [message])
        raw = POOL_MODELS["controls_333hz"]
        self.assertEqual(self.session.scalar(select(func.count()).select_from(raw)), 1)
        with self.session.begin_nested():
            with self.assertRaises(ValueError):
                write_snapshots(self.session, [self.message(2_000_010, 99)])
        self.assertEqual(self.session.scalar(select(raw.apps1_travel)), 12.0)

    def test_late_data_updates_historical_windows_and_can_bridge_them(self):
        write_snapshots(self.session, [self.message(3_000_000), self.message(3_600_000)])
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ArtemisRecordingSession)), 2)
        write_snapshots(self.session, [self.message(3_300_000)])
        windows = self.session.scalars(select(ArtemisRecordingSession)).all()
        self.assertEqual(len(windows), 1)
        self.assertEqual((windows[0].start_time, windows[0].end_time), (3_000_000, 3_600_000))
        raw = POOL_MODELS["controls_333hz"]
        self.assertEqual(set(self.session.scalars(select(raw.session_start_ms))), {3_000_000})

    def test_schema_and_gps_have_no_packet_relationship(self):
        inspector = inspect(self.connection)
        self.assertNotIn("packet", inspector.get_table_names(schema="public"))
        for name in POOL_MODELS:
            self.assertEqual(inspector.get_foreign_keys(name, schema="public"), [])
            self.assertNotIn("packet_id", [c["name"] for c in inspector.get_columns(name, schema="public")])
        write_gps(self.session, [{"time": 4_000_010, "lat_precise": 30.123456789, "lon_precise": -97.123456789}])
        self.assertEqual(self.session.scalar(select(ArtemisTransportState.last_packet_id)), 0)

    def test_existing_kafka_writer_decodes_timestamped_artemis_without_packet_id(self):
        from stack.processors.db_writer.main import write_batch
        from stack.ingest.protobuf.artemis_pb2 import ArtemisSensorData
        message = ArtemisSensorData(time=6_000_010)
        message.controls.apps1_travel = 0
        message.controls.enable = False
        record = SimpleNamespace(headers=[("car_type", b"Artemis")], value=message.SerializeToString())
        with patch("stack.processors.db_writer.main.get_db", return_value=nullcontext(self.session)):
            write_batch([record], {}, {})
        raw = POOL_MODELS["controls_333hz"]
        values = self.session.execute(select(raw.time, raw.apps1_travel, raw.enable)).one()
        self.assertEqual(tuple(values), (6_000_010, 0.0, False))

    def test_additive_apply_preserves_rows_and_rejects_retypes(self):
        write_snapshots(self.session, [self.message(5_000_010)])
        value = {"format_version": 1, "storage_id": "fixture-addition", "pools": copy.deepcopy(POOLS)}
        pool = next(pool for pool in value["pools"] if pool["table"] == "controls_333hz")
        pool["fields"].append({"name": "fixture_sensor", "path": [4, 99999], "sql_type": "double precision", "aggregate": "avg"})
        self.assertIn("Add nullable controls_333hz.fixture_sensor", changes(self.connection, value))
        self.connection.exec_driver_sql(storage.update_sql(value))
        self.assertEqual(changes(self.connection, value), [])
        table = POOL_MODELS["controls_333hz"].__table__
        self.assertEqual(self.session.scalar(select(func.count()).select_from(table)), 1)
        changed = copy.deepcopy(value)
        next(pool for pool in changed["pools"] if pool["table"] == "controls_333hz")["fields"][0]["sql_type"] = "integer"
        with self.assertRaises(ValueError):
            changes(self.connection, changed)


if __name__ == "__main__":
    unittest.main()
