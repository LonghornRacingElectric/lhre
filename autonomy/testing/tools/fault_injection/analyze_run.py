#!/usr/bin/env python3
"""
Score one fault-injection run from its bag and write its row to a CSV.

Times are sim seconds: every message's receive time is mapped through the
recorded /clock, so latencies stay honest when the sim runs slower than
real time. The fault instant comes from fault_injector's /lhr/sim/fault
event; for the geofence case it is the first ground-truth sample outside
the fence, and a clean run has none. A rerun replaces the row with the
same id.

    python3 analyze_run.py <bag dir> --id ID --case KIND --csv results.csv
"""

import argparse
import bisect
import csv
import math
import os
import sys

from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message

# The PDR stop formula, d = v (T + 0.2 s) + v^2 / 2a: T is the measured
# detection latency, 0.2 s the brake apply time, a the spring brake's floor.
APPLY_S = 0.2
DECEL = 6.0
STOPPED = 0.1  # m/s
MOVED = 0.5  # m/s: rolling again after the stop fails the run
MIN_DRIVE_M = 10.0  # less than this before the fault and the run proves nothing

FIELDS = [
    'id', 'case', 'track', 'seed', 'v_max', 'result', 'expected', 'tripped',
    'match', 'v_at_fault', 'trip_latency_s', 'emergency_latency_s',
    'stop_time_s', 'stop_dist_m', 'pdr_dist_m', 'within_pdr',
    'max_v_after_stop', 'false_trip', 'drove_m',
]

# result: what the run shows. Only the first four (and the two clean ones)
# are evidence; the rest say the run itself went wrong.
#   caught       the expected check tripped after the fault
#   wrong_check  another check tripped after the fault
#   missed       the fault ran and nothing tripped
#   early_trip   something tripped before the fault: a false trip
#   clean        a clean lap that drove and never tripped
#   false_trip   a clean lap that tripped
#   no_drive     never reached DRIVING, or barely moved before the fault
#   no_fault     drove, but the fault never started (RUN_S too short?)
#   error        the bag could not be read


def read(bag):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=bag, storage_id='mcap'),
                ConverterOptions('', ''))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    out = {}
    while reader.has_next():
        topic, data, t = reader.read_next()
        msg = deserialize_message(data, get_message(types[topic]))
        out.setdefault(topic, []).append((t * 1e-9, msg))
    return out


def sim_time(clock):
    """Return f(receive time) -> sim time, from the recorded /clock."""
    if not clock:
        return lambda t: t
    wall = [w for w, _ in clock]
    sim = [m.clock.sec + m.clock.nanosec * 1e-9 for _, m in clock]

    def convert(t):
        i = max(0, bisect.bisect_right(wall, t) - 1)
        return sim[i] + (t - wall[i])
    return convert


def path_length(samples, start, end):
    pts = [(x, y) for t, x, y, _ in samples if start <= t <= end]
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def score(data, case, fence):
    """Score one run from {topic: [(receive time, msg)]}."""
    to_sim = sim_time(data.get('/clock', []))
    mission = [(to_sim(t), m.data) for t, m in data.get('/lhr/mission/status', [])]
    truth = [(to_sim(t), m.pose.pose.position.x, m.pose.pose.position.y,
              math.hypot(m.twist.twist.linear.x, m.twist.twist.linear.y))
             for t, m in data.get('/lhr/vehicle/odom_truth', [])]
    trip = next(((to_sim(t), m.message)
                 for t, m in data.get('/lhr/safety/status', [])
                 if any(kv.key == 'latched' and kv.value == 'True'
                        for kv in m.values)), None)
    emergency = next((t for t, s in mission if s == 'EMERGENCY'), None)
    driving = next((t for t, s in mission if s == 'DRIVING'), None)

    fault_t, expected = None, ''
    for t, m in data.get('/lhr/sim/fault', []):
        edge, _, exp = m.data.split()
        if edge == 'start':
            fault_t, expected = to_sim(t), exp
            break
    if case == 'geofence':
        expected = 'GEOFENCE'
        armed = next((t for t, s in mission if s == 'READY'), None)
        start = next(((x, y) for t, x, y, _ in truth
                      if armed is not None and t >= armed), None)
        if start:
            fault_t = next((t for t, x, y, _ in truth if t > armed and math.hypot(
                x - start[0], y - start[1]) > fence), None)

    end = truth[-1][0] if truth else 0.0
    first = min(t for t in (trip and trip[0], fault_t, end) if t is not None)
    drove = path_length(truth, driving, first) if driving is not None else 0.0
    early = trip is not None and (fault_t is None or trip[0] < fault_t)
    if case == 'geofence' and trip and trip[1] == 'GEOFENCE':
        early = False  # it trips on the estimate, which may cross first

    if driving is None:
        result = 'no_drive'
    elif case == 'clean':
        result = ('false_trip' if trip else
                  'no_drive' if drove < MIN_DRIVE_M else 'clean')
    elif early:
        result = 'early_trip'
    elif drove < MIN_DRIVE_M:
        result = 'no_drive'
    elif trip is None:
        result = 'missed' if fault_t is not None else 'no_fault'
    else:
        result = 'caught' if trip[1] == expected else 'wrong_check'

    row = {'case': case, 'result': result, 'expected': expected,
           'tripped': trip[1] if trip else '', 'drove_m': f'{drove:.1f}',
           'match': str(result == 'caught'),
           'false_trip': str(result in ('early_trip', 'false_trip'))}
    if result not in ('caught', 'wrong_check') or fault_t is None:
        return row

    v = next((s for t, _, _, s in reversed(truth) if t <= fault_t), math.nan)
    stop = next((t for t, _, _, s in truth if t > trip[0] and s < STOPPED), None)
    latency = trip[0] - fault_t
    pdr = v * (latency + APPLY_S) + v * v / (2 * DECEL)
    row.update({
        'v_at_fault': f'{v:.2f}',
        'trip_latency_s': f'{latency:.3f}',
        'emergency_latency_s': (f'{emergency - trip[0]:.3f}'
                                if emergency is not None else ''),
        'pdr_dist_m': f'{pdr:.2f}',
        'within_pdr': 'False',
    })
    if stop is not None:
        dist = path_length(truth, fault_t, stop)
        # A car that rolls off again (a controller resuming on stale state)
        # has not stopped, however short the first stop was.
        after = max((s for t, _, _, s in truth if t > stop), default=0.0)
        row.update({'stop_time_s': f'{stop - fault_t:.2f}',
                    'stop_dist_m': f'{dist:.2f}',
                    'max_v_after_stop': f'{after:.2f}',
                    'within_pdr': str(dist <= pdr and after <= MOVED)})
    return row


def save(path, row):
    """Write row into the CSV, replacing an earlier row with the same id."""
    rows = []
    if os.path.exists(path):
        with open(path, newline='') as f:
            rows = [r for r in csv.DictReader(f) if r.get('id') != row['id']]
    rows.append(row)
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, restval='',
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument('bag')
    ap.add_argument('--id', required=True)
    ap.add_argument('--case', required=True)
    ap.add_argument('--track', default='')
    ap.add_argument('--seed', default='')
    ap.add_argument('--v-max', default='')
    ap.add_argument('--fence', type=float, default=20.0)
    ap.add_argument('--csv', required=True)
    args = ap.parse_args()

    status = 0
    try:
        row = score(read(args.bag), args.case, args.fence)
    except Exception as e:  # a bad bag still gets a row, so it is not lost
        print(f'analyze_run: {args.bag}: {e}', file=sys.stderr)
        row, status = {'case': args.case, 'result': 'error'}, 1
    row.update({'id': args.id, 'track': args.track, 'seed': args.seed,
                'v_max': args.v_max})
    save(args.csv, row)
    print('  '.join(f'{k}={row[k]}' for k in FIELDS if row.get(k)))
    return status


if __name__ == '__main__':
    sys.exit(main())
