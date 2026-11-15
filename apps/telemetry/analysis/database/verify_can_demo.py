"""Observe externally generated CAN telemetry through the existing gateway API."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from analysis.query_client import GatewayClient, QueryError


SIGNALS = [
    'controls.apps1_travel', 'controls.apps2_travel', 'controls.commanded_torque',
    'pack.hv_pack_v', 'pack.hv_c', 'thermal.ambient_temp', 'thermal.inverter_temp',
]


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds')


def identity(row):
    return (row['session_start_ms'], row['sample_time_ns'], row['signal'])


def observe(client, signals, duration):
    catalog = client.discover()
    available = {s['signal'] for s in catalog['signals']}
    if not set(signals) <= available:
        raise ValueError('Signals missing from deployed schema: ' + ', '.join(sorted(set(signals) - available)))
    start_ns = time.time_ns()
    deadline = time.monotonic() + duration
    counts = Counter()
    captured = {}
    ages = []
    stream = client.stream({'signals': signals, 'agg': 'raw', 't0': iso(start_ns // 1_000_000),
                            't1': 'live', 'window_ms': 5000, 'page_size': 10000}, timeout=duration + 5)
    try:
        for event in stream:
            if event['type'] in ('gap', 'error'):
                raise RuntimeError(event.get('message', 'Live telemetry is incomplete'))
            page = event.get('page', {})
            if page.get('schema_version', catalog['schema_version']) != catalog['schema_version']:
                raise RuntimeError('Server schema changed; restart verification')
            for row in page.get('data', []):
                stamp = int(row['sample_time_ns'])
                # The writer assigns new recording sessions after the first live arrivals.
                if row['session_start_ms'] == '0':
                    continue
                if stamp < start_ns or row['signal'] not in signals or identity(row) in captured:
                    continue
                age = (time.time_ns() - stamp) / 1_000_000
                if age < -2000:
                    raise RuntimeError('BEVO sample clock is ahead; synchronize both laptop clocks')
                if age > 2000 or counts[row['signal']] >= 100:
                    continue
                captured[identity(row)] = row
                counts[row['signal']] += 1
                ages.append(age)
            if time.monotonic() >= deadline:
                break
    finally:
        stream.close()
    missing = [s for s in signals if counts[s] < 2]
    if missing:
        raise RuntimeError('No fresh repeated observations for: ' + ', '.join(missing))
    return {'format_version': 1, 'schema_version': catalog['schema_version'], 'signals': signals,
            'observations': list(captured.values()), 'max_sample_age_ms': max(ages)}


def verify_history(client, evidence, timeout=30, require_postgres=False):
    if evidence.get('format_version') != 1 or not evidence.get('observations'):
        raise ValueError('Expected nonempty CAN demo evidence, format_version 1')
    if client.discover()['schema_version'] != evidence['schema_version']:
        raise ValueError('Evidence belongs to a different server schema')
    expected = {identity(row): row['value'] for row in evidence['observations']}
    stamps = [int(row['sample_time_ns']) for row in evidence['observations']]
    query = {'signals': evidence['signals'], 'agg': 'raw',
             't0': iso(min(stamps) // 1_000_000), 't1': iso(max(stamps) // 1_000_000 + 1)}
    deadline = time.monotonic() + timeout
    while True:
        try:
            pages = list(client.pages(query))
        except QueryError as error:
            if error.code != 'query_changed' or time.monotonic() >= deadline:
                raise
            time.sleep(.2)
            continue
        actual = {identity(row): row['value'] for page in pages for row in page['data']}
        for key, value in expected.items():
            if key in actual and (type(actual[key]) is not type(value) or actual[key] != value):
                raise RuntimeError('Historical value differs from live evidence: ' + repr(key))
        sources = sorted({source for page in pages for source in page.get('sources', [])})
        provisional = any(page.get('provisional') for page in pages)
        if expected.keys() <= actual.keys():
            if require_postgres and (provisional or sources != ['postgres']):
                raise RuntimeError('Expected a cold Postgres-only read; stop the sender, close live clients, restart the gateway and repeat')
            return {'matched_observations': len(expected), 'sources': sources,
                    'provisional': provisional, 'postgres_verified': sources == ['postgres'] and not provisional,
                    't0': query['t0'], 't1': query['t1']}
        if time.monotonic() >= deadline:
            raise TimeoutError('Live observations did not become complete historical results')
        time.sleep(.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--evidence', type=Path, help='Capture live evidence here, then verify historical reads.')
    mode.add_argument('--check', type=Path, help='Recheck previously captured evidence.')
    parser.add_argument('--duration', type=float, default=15)
    parser.add_argument('--signals', nargs='+', default=SIGNALS)
    parser.add_argument('--url', help='Defaults to QUERY_GATEWAY_URL.')
    parser.add_argument('--require-postgres', action='store_true', help='Require the recheck to read Postgres.')
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error('--duration must be finite and positive')
    if args.require_postgres and not args.check:
        parser.error('--require-postgres requires --check')
    with GatewayClient(args.url) as client:
        if args.check:
            evidence = json.loads(args.check.read_text())
        else:
            evidence = observe(client, args.signals, args.duration)
            args.evidence.write_text(json.dumps(evidence, indent=2) + '\n')
        result = verify_history(client, evidence, require_postgres=args.require_postgres)
        print(json.dumps({**result, 'signals': evidence['signals'],
                          'max_sample_age_ms': evidence['max_sample_age_ms']}))


if __name__ == '__main__':
    main()
