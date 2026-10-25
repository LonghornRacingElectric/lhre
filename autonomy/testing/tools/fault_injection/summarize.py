#!/usr/bin/env python3
"""
Turn results.csv into the markdown tables for the campaign README.

    python3 summarize.py results.csv > summary.md
"""

import csv
from statistics import median
import sys

# Runs that say something about the safety loop; see analyze_run.py.
FAULT_RESULTS = ('caught', 'wrong_check', 'missed', 'early_trip')
CLEAN_RESULTS = ('clean', 'false_trip')


def fmt(values, how):
    nums = [float(v) for v in values if v not in ('', 'nan')]
    return f'{how(nums):.2f}' if nums else '-'


def main():
    rows = list(csv.DictReader(open(sys.argv[1], newline='')))
    faulted = [r for r in rows if r['result'] in FAULT_RESULTS]
    clean = [r for r in rows if r['result'] in CLEAN_RESULTS]
    unscored = [r for r in rows
                if r['result'] not in FAULT_RESULTS + CLEAN_RESULTS]

    print('| Fault | v_max (m/s) | Runs | Caught by expected check | '
          'Tripped before the fault | Trip latency, median (s) | '
          'Stop distance, max (m) | PDR formula, max (m) | Within formula |')
    print('|---|---|---|---|---|---|---|---|---|')
    groups = {}
    for r in faulted:
        groups.setdefault((r['case'], r['v_max']), []).append(r)
    for (case, v), rs in sorted(groups.items()):
        caught = sum(r['result'] == 'caught' for r in rs)
        early = sum(r['result'] == 'early_trip' for r in rs)
        within = sum(r['within_pdr'] == 'True' for r in rs)
        print(f'| `{case}` | {v} | {len(rs)} | {caught}/{len(rs)} | {early} | '
              f"{fmt([r['trip_latency_s'] for r in rs], median)} | "
              f"{fmt([r['stop_dist_m'] for r in rs], max)} | "
              f"{fmt([r['pdr_dist_m'] for r in rs], max)} | "
              f'{within}/{len(rs)} |')

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


if __name__ == '__main__':
    main()
