"""Manual integration checks against isolated pilot database instances."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import unittest
from uuid import uuid4

import psycopg2

import storage
from workload import observations, signals


class StorageIntegrationTest(unittest.TestCase):
    def test_postgres_and_timescale(self):
        for backend in ('postgres', 'timescale'):
            with self.subTest(backend=backend):
                dsn = os.environ.get('PILOT_' + backend.upper() + '_DSN')
                self.assertIsNotNone(dsn, 'Set both PILOT_POSTGRES_DSN and PILOT_TIMESCALE_DSN')
                connection = storage.connect(dsn)
                try:
                    storage.initialize(connection, backend)
                    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
                    session_id = uuid4()
                    definitions = signals(10, 1, 2)
                    storage.register_session(connection, session_id, start, 2, definitions, 1)
                    rows = list(observations(session_id, start, 2, definitions))
                    self.assertEqual(storage.insert_observations(connection, rows), 26)
                    self.assertEqual(storage.insert_observations(connection, list(reversed(rows))), 0)
                    with connection, connection.cursor() as cursor:
                        cursor.execute('''SELECT signal_id, count(*) FROM pilot.measurements
                            WHERE session_id = %s GROUP BY signal_id''', (str(session_id),))
                        self.assertEqual(dict(cursor.fetchall()), {1: 20, 2: 2, 3: 4})
                        cursor.execute('''SELECT value_int, value_float FROM pilot.measurements
                            WHERE session_id = %s AND signal_id = 1 ORDER BY sample_id LIMIT 2''',
                                       (str(session_id),))
                        values = cursor.fetchall()
                        self.assertEqual(values[0], values[1])
                        self.assertIsNone(values[0][1])
                    storage.refresh(connection, backend, start, start + timedelta(seconds=2))
                    # Late source observations must change the summary after refresh.
                    late = replace(rows[0], sample_id=999, value=1234)
                    self.assertEqual(storage.insert_observations(connection, [late]), 1)
                    storage.refresh(connection, backend, start, start + timedelta(seconds=2))
                    with connection, connection.cursor() as cursor:
                        cursor.execute(*storage.summary_query(backend, session_id, 1, start, start + timedelta(seconds=2)))
                        summaries = cursor.fetchall()
                        self.assertEqual(sum(row[2] for row in summaries), 21)
                        cursor.execute(*storage.raw_query(backend, session_id, 1, start, start + timedelta(seconds=2)))
                        raw = cursor.fetchall()
                        weighted = sum(row[1] * row[2] for row in summaries) / sum(row[2] for row in summaries)
                        self.assertAlmostEqual(weighted, sum(row[1] for row in raw) / len(raw))
                    # Metadata enforces the signal's type, not just nullable columns.
                    bad = replace(rows[0], sample_id=1000, value_type='float', value=1.5)
                    with self.assertRaises(psycopg2.IntegrityError):
                        storage.insert_observations(connection, [bad])
                    invalid = replace(rows[0], sample_id=1001, ts=start + timedelta(seconds=1, microseconds=999999),
                                      value=0, quality='invalid')
                    self.assertEqual(storage.insert_observations(connection, [invalid]), 1)
                    with connection, connection.cursor() as cursor:
                        cursor.execute(*storage.latest_query(backend, session_id, 1))
                        self.assertNotEqual(cursor.fetchone()[1], 0)
                        cursor.execute(*storage.raw_query(backend, session_id, 1, start, start + timedelta(seconds=2)))
                        self.assertEqual(len(cursor.fetchall()), 21)
                    with connection, connection.cursor() as cursor:
                        cursor.execute('SELECT count(*) FROM pilot.fault_events WHERE session_id = %s', (str(session_id),))
                        self.assertEqual(cursor.fetchone()[0], 0)
                finally:
                    connection.close()

    def test_fractional_start_refreshes_complete_boundary_buckets(self):
        connection = storage.connect(os.environ['PILOT_TIMESCALE_DSN'])
        try:
            storage.initialize(connection, 'timescale')
            start = datetime(2026, 2, 1, microsecond=500000, tzinfo=timezone.utc)
            session_id = uuid4()
            definitions = signals(10, 1, 2)
            storage.register_session(connection, session_id, start, 2, definitions, 1)
            rows = list(observations(session_id, start, 2, definitions))
            storage.insert_observations(connection, rows)
            storage.refresh(connection, 'timescale', start, start + timedelta(seconds=2))
            with connection, connection.cursor() as cursor:
                cursor.execute('''SELECT sum(sample_count) FROM pilot.measurements_1s
                    WHERE session_id = %s''', (str(session_id),))
                self.assertEqual(cursor.fetchone()[0], 26)
        finally:
            connection.close()


if __name__ == '__main__':
    unittest.main()
