"""CLI for the isolated sender, correctness checks, and three-way evaluation."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
import random
import sys
import time
from uuid import UUID, uuid4

import psycopg2

import storage
from workload import SCHEMA_ID, batches, observations, signals, snapshots

DEFAULT_DSNS = {
    'postgres': 'host=127.0.0.1 port=55432 dbname=pilot user=pilot password=pilot_local',
    'timescale': 'host=127.0.0.1 port=55433 dbname=pilot user=pilot password=pilot_local',
    'snapshot': 'host=127.0.0.1 port=55434 dbname=pilot user=pilot password=pilot_local',
}


def open_target(target):
    return storage.connect(os.getenv('PILOT_' + target.upper() + '_DSN', DEFAULT_DSNS[target]))


def start_time(value, seconds, realtime=False):
    if value:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('--start must include a timezone')
        return parsed.astimezone(timezone.utc)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now if realtime else now - timedelta(seconds=seconds)


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('Percentiles need at least one measurement')
    index = fraction * (len(ordered) - 1)
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def load(connection, backend, session_id, start, args, definitions):
    source = snapshots if backend == 'snapshot' else observations
    items = source(session_id, start, args.seconds, definitions, args.seed)
    inserted = 0
    write_seconds = 0.0
    wall_start = time.perf_counter()
    for batch in batches(items, args.batch_size):
        before = time.perf_counter()
        if backend == 'snapshot':
            inserted += storage.insert_snapshots(connection, batch)
        else:
            inserted += storage.insert_observations(connection, batch)
        write_seconds += time.perf_counter() - before
    return inserted, time.perf_counter() - wall_start, write_seconds


def verify(connection, session_id, seconds, definitions):
    with connection, connection.cursor() as cursor:
        cursor.execute('''SELECT signal_id, count(*), count(DISTINCT sample_id), min(sample_id), max(sample_id)
            FROM pilot.measurements WHERE session_id = %s GROUP BY signal_id''', (str(session_id),))
        found = {row[0]: row[1:] for row in cursor.fetchall()}
    expected = {signal.signal_id: (seconds * signal.hz, seconds * signal.hz, 0, seconds * signal.hz - 1)
                for signal in definitions}
    if found != expected:
        raise ValueError(f'Sample identity/count mismatch: expected {expected}, found {found}')
    return {signal.name: found[signal.signal_id][0] for signal in definitions}


def send(args):
    targets = ('postgres', 'timescale') if args.target == 'both' else (args.target,)
    definitions = signals(args.speed_hz, args.temperature_hz, args.voltage_hz)
    session_id = args.session or uuid4()
    start = start_time(args.start, args.seconds, args.realtime)
    connections = {}
    try:
        for target in targets:
            connection = open_target(target)
            connections[target] = connection
            storage.initialize(connection, target)
            storage.register_session(connection, session_id, start, args.seconds, definitions, args.seed)
        clock_start = time.monotonic()
        pending = []
        count = 0
        last_flush = clock_start

        def flush():
            nonlocal count, last_flush
            if not pending:
                return
            for connection in connections.values():
                storage.insert_observations(connection, pending)
            count += len(pending)
            pending.clear()
            last_flush = time.monotonic()
            print(json.dumps({'session_id': str(session_id), 'observations_sent': count}), flush=True)

        for row in observations(session_id, start, args.seconds, definitions, args.seed):
            if args.realtime:
                due = clock_start + (row.ts - start).total_seconds()
                time.sleep(max(0.0, due - time.monotonic()))
            pending.append(row)
            if len(pending) >= args.batch_size or time.monotonic() - last_flush >= 1:
                flush()
        flush()
        for target, connection in connections.items():
            counts = verify(connection, session_id, args.seconds, definitions)
            storage.refresh(connection, target, start, start + timedelta(seconds=args.seconds))
            print(json.dumps({'target': target, 'session_id': str(session_id), 'verified_counts': counts}))
    finally:
        for connection in connections.values():
            connection.close()


def query_timings(connection, backend, session_id, start, args):
    timings = {name: [] for name in ('raw', 'latest', 'summary')}
    rng = random.Random(args.seed)
    with connection.cursor() as cursor:
        for iteration in range(args.repeats + 3):
            signal_id = rng.choice((1, 2, 3))
            offset = rng.randrange(max(1, args.seconds - min(60, args.seconds) + 1))
            begin = start + timedelta(seconds=offset)
            end = begin + timedelta(seconds=min(60, args.seconds))
            queries = {
                'raw': storage.raw_query(backend, session_id, signal_id, begin, end),
                'latest': storage.latest_query(backend, session_id, signal_id),
                'summary': storage.summary_query(backend, session_id, signal_id, begin, end),
            }
            order = list(queries)
            rng.shuffle(order)
            for name in order:
                before = time.perf_counter_ns()
                cursor.execute(*queries[name])
                cursor.fetchall()
                elapsed_ms = (time.perf_counter_ns() - before) / 1_000_000
                if iteration >= 3:
                    timings[name].append(elapsed_ms)
    connection.commit()
    return {name: {'n': len(values), 'p95_ms': percentile(values, 0.95),
                   'p99_ms': percentile(values, 0.99)} for name, values in timings.items()}


def benchmark(args):
    definitions = signals(args.speed_hz, args.temperature_hz, args.voltage_hz)
    start = start_time(args.start, args.seconds)
    session_id = uuid4()
    results = {'status': 'pilot_only', 'session_id': str(session_id), 'schema_id': SCHEMA_ID,
               'start': start.isoformat(), 'seconds': args.seconds, 'seed': args.seed,
               'batch_size': args.batch_size, 'rates_hz': {s.name: s.hz for s in definitions},
               'input_observations': sum(s.hz for s in definitions) * args.seconds,
               'query_latency': 'warm client round trips, including fetching results',
               'cases': {}}
    connections = {}
    try:
        # Refuse nonempty pilot tables instead of deleting somebody's demo data.
        for backend in ('snapshot', 'postgres', 'timescale'):
            connection = open_target(backend)
            connections[backend] = connection
            storage.initialize(connection, backend)
            table = 'public.packet' if backend == 'snapshot' else 'pilot.measurements'
            with connection, connection.cursor() as cursor:
                cursor.execute('SELECT EXISTS (SELECT FROM ' + table + ')')
                if cursor.fetchone()[0]:
                    raise ValueError(f'{backend} is not empty; use a fresh Compose project for benchmarking')
        for backend, connection in connections.items():
            storage.register_session(connection, session_id, start, args.seconds, definitions, args.seed)
            if backend == 'timescale':
                with connection, connection.cursor() as cursor:
                    cursor.execute("SELECT remove_continuous_aggregate_policy('pilot.measurements_1s', if_exists => true)")
            before_sizes = storage.relation_sizes(connection, backend)
            wal_start = storage.wal_position(connection)
            inserted, wall_seconds, write_seconds = load(connection, backend, session_id, start, args, definitions)
            wal_growth = storage.wal_bytes(connection, wal_start)
            refresh_seconds = storage.refresh(connection, backend, start, start + timedelta(seconds=args.seconds))
            with connection, connection.cursor() as cursor:
                cursor.execute('ANALYZE')
                cursor.execute('SELECT version()')
                version = cursor.fetchone()[0]
            sizes = storage.relation_sizes(connection, backend)
            case = {'server_version': version, 'synchronous_commit': 'on', 'fsync': 'on',
                    'full_page_writes': 'on', 'ingest_wall_seconds': wall_seconds,
                    'database_write_seconds': write_seconds, 'records_inserted': inserted,
                    'record_kind': 'snapshots' if backend == 'snapshot' else 'observations',
                    'records_per_write_second': inserted / write_seconds,
                    'sql_rows_inserted': inserted * (4 if backend == 'snapshot' else 1),
                    'wal_ingest_bytes': wal_growth, 'aggregate_refresh_seconds': refresh_seconds,
                    'sizes': sizes, 'query_latency': query_timings(connection, backend, session_id, start, args)}
            with connection, connection.cursor() as cursor:
                cursor.execute('''SELECT name, setting, unit FROM pg_settings WHERE name IN
                    ('shared_buffers', 'max_connections', 'fsync', 'full_page_writes', 'synchronous_commit')''')
                case['database_settings'] = {name: {'setting': setting, 'unit': unit}
                                             for name, setting, unit in cursor.fetchall()}
            if backend != 'snapshot':
                case['verified_counts'] = verify(connection, session_id, args.seconds, definitions)
                first = next(observations(session_id, start, args.seconds, definitions, args.seed))
                case['replay_inserted_rows'] = storage.insert_observations(connection, [first, first])
                if case['replay_inserted_rows'] != 0:
                    raise ValueError('Replay created duplicate observations')
                case['projected_raw_growth_bytes_per_day'] = (
                    sizes['raw_total_bytes'] - before_sizes['raw_total_bytes']) * 86400 / args.seconds
            else:
                case['snapshot_hz'] = max(signal.hz for signal in definitions)
                case['cached_field_values'] = inserted * len(definitions)
                case['note'] = 'Existing Orion tables, three populated fields; cached snapshot values are not actual observations'
            results['cases'][backend] = case
            print(f'Benchmarked {backend}: {inserted} rows', file=sys.stderr, flush=True)
        if args.columnstore:
            connection = connections['timescale']
            before = time.perf_counter()
            with storage.maintenance_cursor(connection) as cursor:
                cursor.execute("SELECT show_chunks('pilot.measurements')::text")
                for chunk, in cursor.fetchall():
                    cursor.execute('CALL convert_to_columnstore(%s::regclass)', (chunk,))
            results['cases']['timescale_columnstore'] = {
                'conversion_seconds': time.perf_counter() - before,
                'sizes': storage.relation_sizes(connection, 'timescale'),
                'query_latency': query_timings(connection, 'timescale', session_id, start, args),
                'verified_counts': verify(connection, session_id, args.seconds, definitions),
            }
        print(json.dumps(results, indent=2))
    finally:
        for connection in connections.values():
            connection.close()


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='initialize only the isolated pilot databases')
    init.add_argument('--target', choices=('all', 'snapshot', 'postgres', 'timescale'), default='all')
    for name in ('send', 'benchmark', 'preview'):
        command = commands.add_parser(name)
        command.add_argument('--seconds', type=int, default=60)
        command.add_argument('--speed-hz', type=int, default=100)
        command.add_argument('--temperature-hz', type=int, default=1)
        command.add_argument('--voltage-hz', type=int, default=10)
        command.add_argument('--seed', type=int, default=1)
        command.add_argument('--start', help='ISO 8601 timestamp with timezone; default is near now')
        command.add_argument('--batch-size', type=int, default=500)
        if name == 'send':
            command.add_argument('--target', choices=('both', 'postgres', 'timescale'), default='both')
            command.add_argument('--realtime', action='store_true')
            command.add_argument('--session', type=UUID)
        elif name == 'benchmark':
            command.add_argument('--repeats', type=int, default=100)
            command.add_argument('--columnstore', action='store_true')
    return root


def main():
    arguments = parser()
    args = arguments.parse_args()
    if args.command != 'init' and (args.seconds <= 0 or args.batch_size <= 0):
        arguments.error('seconds and batch size must be positive')
    if args.command == 'benchmark' and args.repeats <= 0:
        arguments.error('repeats must be positive')
    try:
        if args.command == 'init':
            for target in ('snapshot', 'postgres', 'timescale') if args.target == 'all' else (args.target,):
                connection = open_target(target)
                try:
                    storage.initialize(connection, target)
                finally:
                    connection.close()
                print('Initialized ' + target)
        elif args.command == 'send':
            send(args)
        elif args.command == 'benchmark':
            benchmark(args)
        else:
            definitions = signals(args.speed_hz, args.temperature_hz, args.voltage_hz)
            start = start_time(args.start, args.seconds)
            print(json.dumps({'schema_id': SCHEMA_ID, 'rates_hz': {s.name: s.hz for s in definitions},
                              'expected_observations': {s.name: s.hz * args.seconds for s in definitions}}))
            from itertools import islice
            for row in islice(observations(uuid4(), start, args.seconds, definitions, args.seed), 12):
                print(json.dumps(row.__dict__, default=str))
    except (ValueError, psycopg2.Error) as error:
        # Do not print DSNs: callers may use credentials other than the local defaults.
        print(f'Pilot failed ({type(error).__name__}): {error}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
