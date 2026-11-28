#!/usr/bin/env python3
"""
Turn results.csv into the markdown tables for the campaign README.

    python3 summarize.py results.csv > summary.md
"""

import argparse
from collections import Counter
import csv
from pathlib import Path
from statistics import median
import sys

# Runs that say something about the safety loop; see analyze_run.py.
FAULT_RESULTS = ('caught', 'wrong_check', 'missed', 'early_trip')
CLEAN_RESULTS = ('clean', 'false_trip')


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(values, how):
    # Markers such as 'never' are not numbers; leave them out of the statistic.
    nums = [n for n in (number(v) for v in values) if n is not None and n == n]
    return f'{how(nums):.2f}' if nums else '-'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('csv', type=Path)
    parser.add_argument('--expected', type=Path, help='one planned run ID per line')
    args = parser.parse_args()
    rows = []
    if args.csv.is_file():
        with args.csv.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
    elif args.expected is None:
        parser.error(f'missing results: {args.csv}')
    faulted = [r for r in rows if r['result'] in FAULT_RESULTS]
    clean = [r for r in rows if r['result'] in CLEAN_RESULTS]
    unscored = [r for r in rows
                if r['result'] not in FAULT_RESULTS + CLEAN_RESULTS]

    print('| Fault | v_max (m/s) | Speed at fault, median (m/s) | Runs | '
          'Caught by expected check | Tripped before the fault | '
          'Trip latency, median (s) | Stop distance, max (m) | '
          'PDR formula, max (m) | Within formula | Within trip limit |')
    print('|---|---|---|---|---|---|---|---|---|---|---|')
    groups = {}
    for r in faulted:
        groups.setdefault((r['case'], r['v_max']), []).append(r)
    for (case, v), rs in sorted(groups.items()):
        caught = sum(r['result'] == 'caught' for r in rs)
        early = sum(r['result'] == 'early_trip' for r in rs)
        within = sum(r['within_pdr'] == 'True' for r in rs)
        timely = sum(r.get('within_trip_limit') == 'True' for r in rs)
        timing = f'{timely}/{len(rs)}' if any(r.get('within_trip_limit') for r in rs) else '-'
        print(f"| `{case}` | {v} | {fmt([r['v_at_fault'] for r in rs], median)} | "
              f'{len(rs)} | {caught}/{len(rs)} | {early} | '
              f"{fmt([r['trip_latency_s'] for r in rs], median)} | "
              f"{fmt([r['stop_dist_m'] for r in rs], max)} | "
              f"{fmt([r['pdr_dist_m'] for r in rs], max)} | "
              f'{within}/{len(rs)} | {timing} |')

    freezes = [r for r in faulted
               if r['case'] == 'steer_freeze' and r.get('latency_from_visible_s')]
    if freezes:
        # A frozen actuator only shows once the command moves off the
        # frozen angle; the STEER_TRACKING budget runs from there (#208).
        print()
        print(f'`steer_freeze`: visible {fmt([r["visible_after_s"] for r in freezes], median)} s '
              f'after the freeze (median), then tripped within '
              f'{fmt([r["latency_from_visible_s"] for r in freezes], max)} s.')

    slow = [r for r in faulted if r.get('at_speed') == 'False']
    if slow:
        # A tier is only tested at its speed (#207); say which runs were not.
        print()
        print('Faulted under 90% of v_max, so they do not test their tier: '
              + ', '.join(f"{r['id']} ({r['v_at_fault']} m/s)" for r in slow))

    if clean:
        trips = [r for r in clean if r['result'] == 'false_trip']
        print()
        print(f'Clean laps: {len(clean)} runs, {len(trips)} false trips'
              + (': ' + ', '.join(f"{r['id']} ({r['tripped']})" for r in trips)
                 if trips else '.'))
    if unscored:
        print()
        print('Not scored (rerun these): '
              + ', '.join(f"{r['id']} ({r['result']})" for r in unscored))

    if args.expected is None:
        return 0
    expected = args.expected.read_text().split()
    counts = Counter(row['id'] for row in rows)
    passing = {row['id'] for row in rows if row.get('passed') == 'True'}
    failures = [run for run in expected if counts[run] != 1 or run not in passing]
    extra = sorted(counts.keys() - set(expected))
    complete = bool(expected) and len(expected) == len(set(expected))
    ok = complete and not failures and not extra
    print(f'\nAcceptance: {"PASS" if ok else "FAIL"}, '
          f'{len(expected) - len(failures)} of {len(expected)} planned rows passed.')
    if failures:
        print('Missing, duplicate or failed rows: ' + ', '.join(failures))
    if extra:
        print('Unexpected rows: ' + ', '.join(extra))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
