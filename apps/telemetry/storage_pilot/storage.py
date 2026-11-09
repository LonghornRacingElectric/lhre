"""The pilot's direct, durable batching storage path."""

from pathlib import Path
from contextlib import contextmanager
from datetime import timedelta
import time

import psycopg2
from psycopg2.extras import execute_values

from workload import SCHEMA_ID

ROOT = Path(__file__).resolve().parent
BASELINE_TABLES = ('public.packet', 'public.controls', 'public.thermal', 'public.pack')
SNAPSHOT_COLUMNS = {
    1: ('controls', 'motor_speed'), 2: ('thermal', 'ambient_temp'),
    3: ('pack', 'hv_pack_v'),
}


def connect(dsn):
    connection = psycopg2.connect(dsn, connect_timeout=10, options='-c timezone=UTC -c synchronous_commit=on')
    with connection.cursor() as cursor:
        for setting in ('fsync', 'full_page_writes', 'synchronous_commit'):
            cursor.execute('SHOW ' + setting)
            if cursor.fetchone()[0] != 'on':
                connection.close()
                raise ValueError(f'{setting} must be on for the pilot')
    connection.commit()
    return connection


def initialize(connection, backend):
    with connection, connection.cursor() as cursor:
        if backend == 'timescale':
            cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb VERSION '2.24.0'")
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            if cursor.fetchone()[0] != '2.24.0':
                raise ValueError('This pilot requires TimescaleDB 2.24.0')
        cursor.execute((ROOT / 'metadata.sql').read_text())
        if backend == 'snapshot':
            cursor.execute("SELECT to_regclass('public.packet')")
            if cursor.fetchone()[0] is None:
                # The deployed DDL assigns a helper function to this role.
                # The isolated baseline needs no production login credentials.
                cursor.execute("""DO $$ BEGIN
                    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'electric') THEN
                        CREATE ROLE electric NOLOGIN;
                    END IF;
                END $$""")
                cursor.execute((ROOT.parent / 'stack/ingest/orion_db_init.sql').read_text())
        else:
            cursor.execute((ROOT / 'measurements.sql').read_text())
            cursor.execute((ROOT / ('timescale.sql' if backend == 'timescale' else 'postgres.sql')).read_text())
        cursor.execute("""DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pilot_reader') THEN
                CREATE ROLE pilot_reader LOGIN PASSWORD 'pilot_readonly';
            END IF;
        END $$""")
        cursor.execute('GRANT USAGE ON SCHEMA pilot TO pilot_reader')
        cursor.execute('GRANT SELECT ON ALL TABLES IN SCHEMA pilot TO pilot_reader')


def register_session(connection, session_id, start, seconds, definitions, seed):
    with connection, connection.cursor() as cursor:
        cursor.execute(
            'INSERT INTO pilot.sessions VALUES (%s, %s, %s, %s, %s, %s)',
            (str(session_id), 'dummy-car', SCHEMA_ID, start, seconds, seed),
        )
        execute_values(cursor, 'INSERT INTO pilot.signals VALUES %s ON CONFLICT DO NOTHING', [
            (SCHEMA_ID, signal.signal_id, signal.name, signal.unit, signal.value_type)
            for signal in definitions
        ])
        execute_values(cursor, 'INSERT INTO pilot.session_rates VALUES %s', [
            (str(session_id), signal.signal_id, signal.hz) for signal in definitions
        ])


def insert_observations(connection, rows):
    values = [(
        row.session_id, row.schema_id, row.signal_id, row.sample_id, row.ts,
        row.value_type, row.value if row.value_type == 'int' else None,
        row.value if row.value_type == 'float' else None, row.quality,
    ) for row in rows]
    if not values:
        return 0
    with connection, connection.cursor() as cursor:
        # Count only new identities; retries preserve the original typed value.
        result = execute_values(cursor, '''INSERT INTO pilot.measurements
            (session_id, schema_id, signal_id, sample_id, ts, value_type,
             value_int, value_float, quality) VALUES %s
            ON CONFLICT DO NOTHING RETURNING 1''', values, fetch=True)
        return len(result)


def insert_snapshots(connection, rows):
    with connection, connection.cursor() as cursor:
        execute_values(cursor, 'INSERT INTO public.packet (packet_id, time) VALUES %s',
                       [(row[0], row[1]) for row in rows])
        execute_values(cursor, 'INSERT INTO public.controls (packet_id, motor_speed) VALUES %s',
                       [(row[0], row[2]) for row in rows])
        execute_values(cursor, 'INSERT INTO public.thermal (packet_id, ambient_temp) VALUES %s',
                       [(row[0], row[3]) for row in rows])
        execute_values(cursor, 'INSERT INTO public.pack (packet_id, hv_pack_v) VALUES %s',
                       [(row[0], row[4]) for row in rows])
    return len(rows)


def refresh(connection, backend, start, end):
    if backend != 'timescale':
        return 0.0
    started = time.perf_counter()
    begin = start.replace(microsecond=0)
    finish = end.replace(microsecond=0)
    if end.microsecond:
        finish += timedelta(seconds=1)
    with maintenance_cursor(connection) as cursor:
        cursor.execute('CALL refresh_continuous_aggregate(%s, %s, %s)',
                       ('pilot.measurements_1s', begin, finish))
    return time.perf_counter() - started


@contextmanager
def maintenance_cursor(connection):
    """Timescale maintenance procedures manage their own transactions."""
    connection.commit()
    previous = connection.autocommit
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            yield cursor
    finally:
        connection.autocommit = previous


def raw_query(backend, session_id, signal_id, start, end):
    if backend == 'snapshot':
        table, field = SNAPSHOT_COLUMNS[signal_id]
        return (f'''SELECT packet.time, data.{field} FROM public.packet
            JOIN public.{table} AS data USING (packet_id)
            WHERE packet.time >= %s AND packet.time < %s ORDER BY packet.time''',
                (int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
    return ('''SELECT ts, COALESCE(value_float, value_int::double precision)
        FROM pilot.measurements WHERE session_id = %s AND signal_id = %s
        AND ts >= %s AND ts < %s AND quality = 'valid' ORDER BY ts''',
            (str(session_id), signal_id, start, end))


def latest_query(backend, session_id, signal_id):
    if backend == 'snapshot':
        table, field = SNAPSHOT_COLUMNS[signal_id]
        return (f'''SELECT packet.time, data.{field} FROM public.packet
            JOIN public.{table} AS data USING (packet_id) ORDER BY packet.time DESC LIMIT 1''', ())
    return ('''SELECT ts, COALESCE(value_float, value_int::double precision)
        FROM pilot.measurements WHERE session_id = %s AND signal_id = %s
        AND quality = 'valid' ORDER BY ts DESC LIMIT 1''', (str(session_id), signal_id))


def summary_query(backend, session_id, signal_id, start, end):
    if backend == 'snapshot':
        table, field = SNAPSHOT_COLUMNS[signal_id]
        # The deployed viewer averages snapshots, including their cached values.
        return (f'''SELECT floor(packet.time / 1000.0), avg(data.{field}), count(*)
            FROM public.packet JOIN public.{table} AS data USING (packet_id)
            WHERE packet.time >= %s AND packet.time < %s GROUP BY 1 ORDER BY 1''',
                (int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
    return ('''SELECT bucket, sum_value / sample_count, sample_count
        FROM pilot.measurements_1s WHERE session_id = %s AND signal_id = %s
        AND bucket >= %s AND bucket < %s ORDER BY bucket''',
            (str(session_id), signal_id, start, end))


def relation_sizes(connection, backend):
    with connection.cursor() as cursor:
        if backend == 'timescale':
            cursor.execute("SELECT table_bytes, index_bytes, total_bytes FROM hypertable_detailed_size('pilot.measurements')")
            raw = cursor.fetchone()
            cursor.execute("SELECT total_bytes FROM hypertable_detailed_size('pilot.measurements_1s')")
            summary_bytes = cursor.fetchone()[0]
        else:
            relations = BASELINE_TABLES if backend == 'snapshot' else ('pilot.measurements',)
            cursor.execute('''SELECT sum(pg_table_size(name::regclass)),
                sum(pg_indexes_size(name::regclass)), sum(pg_total_relation_size(name::regclass))
                FROM unnest(%s::text[]) AS name''', (list(relations),))
            raw = cursor.fetchone()
            summary_bytes = 0
        cursor.execute('SELECT pg_database_size(current_database())')
        database_bytes = cursor.fetchone()[0]
    connection.commit()
    return dict(table_bytes=int(raw[0] or 0), index_bytes=int(raw[1] or 0),
                raw_total_bytes=int(raw[2] or 0), summary_bytes=int(summary_bytes or 0),
                database_bytes=int(database_bytes))


def wal_position(connection):
    with connection, connection.cursor() as cursor:
        cursor.execute('SELECT pg_current_wal_insert_lsn()::text')
        return cursor.fetchone()[0]


def wal_bytes(connection, start):
    with connection, connection.cursor() as cursor:
        cursor.execute('SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), %s)', (start,))
        return int(cursor.fetchone()[0])
