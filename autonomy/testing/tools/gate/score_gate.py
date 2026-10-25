#!/usr/bin/env python3
"""
Score an MVP gate campaign against docs/plans/mvp-pass-criteria.md.

Reads every run folder run_gate.sh wrote under <out>/runs (metrics.csv from
lhr_metrics, the bag and the launch log) and writes <out>/results.csv, one
row per run, and <out>/summary.md, the pass/fail table. Rerunnable: it only
reads the run folders.

    python3 score_gate.py ~/lhr-test-data/<date>-mvp-gate

Course scoring (completion, off course, cones, CTE, EKF error) is
lhr_metrics' verdict. The bag adds what metrics does not record: distance
from the finish to standstill, that the mission reached FINISHED and that
the car stayed stopped. Times are sim seconds, mapped through /clock.
"""

import bisect
import csv
import math
import os
import re
import sys

from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message

# Thresholds from the pass criteria.
STOP_LIMIT_M = {'autocross': 30.0, 'acceleration': 75.0}
MAX_CTE_M = 1.0
MAX_EKF_ERROR_M = 0.5
LAP_TIME_RATIO = 1.5
COMPLETE = {'autocross': 'lap', 'acceleration': 'mission_finished'}
STOPPED = 0.1  # m/s
MOVED = 0.5  # m/s: rolling again after the stop fails the run

CHECKS = ['completes', 'stops', 'off_course', 'cones', 'cte', 'ekf', 'lap_time', 'health']
FIELDS = (['id', 'kind', 'event', 'seed', 'outcome', 'lap_time_s', 'baseline_lap_s',
           'lap_ratio', 'max_cte', 'off_track_count', 'cones_hit', 'max_pos_error',
           'stop_dist_m', 'stop_time_s', 'max_v_after_stop', 'finished', 'emergency',
           'early_exits', 'invalid_reason'] + CHECKS + ['passed'])
RUN_ID = re.compile(r'^(baseline_)?(autocross|acceleration)(?:_s(\d+))?(?:_r(\d+))?$')


def read(bag):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=bag, storage_id='mcap'), ConverterOptions('', ''))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    out = {}
    while reader.has_next():
        topic, data, t = reader.read_next()
        out.setdefault(topic, []).append((t * 1e-9, deserialize_message(
            data, get_message(types[topic]))))
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


def first_true(samples):
    return next((t for t, m in samples if m.data), None)


def finish_and_stop(topics, event):
    """Distance and time from the finish to standstill, plus how the run ended."""
    to_sim = sim_time(topics.get('/clock', []))
    truth = topics.get('/lhr/vehicle/odom_truth') or topics.get('/lhr/vehicle/odom', [])
    odom = [(to_sim(t), m.pose.pose.position.x, m.pose.pose.position.y,
             math.hypot(m.twist.twist.linear.x, m.twist.twist.linear.y)) for t, m in truth]
    status = [(to_sim(t), m.data) for t, m in topics.get('/lhr/mission/status', [])]
    # The finish: the reference gate for a lap, the nose on the line for
    # acceleration (the mission's own completion).
    finish_topic = ('/lhr/metrics/lap_complete' if event == 'autocross'
                    else '/lhr/mission/complete')
    finish = first_true(topics.get(finish_topic, []))
    result = {'finished': any(s == 'FINISHED' for _, s in status),
              'emergency': any(s == 'EMERGENCY' for _, s in status)}
    if finish is None or not odom:
        return result
    finish = to_sim(finish)
    after = [o for o in odom if o[0] >= finish]
    stop = next((o for o in after if o[3] < STOPPED), None)
    if stop is None:
        return result
    moving = [o for o in after if o[0] <= stop[0]]
    result['stop_dist_m'] = sum(math.dist(a[1:3], b[1:3]) for a, b in zip(moving, moving[1:]))
    result['stop_time_s'] = stop[0] - finish
    result['max_v_after_stop'] = max(o[3] for o in odom if o[0] >= stop[0])
    return result


def early_exits(log_path):
    """Return nodes that exited before teardown began; metrics ending a run is not one."""
    if not os.path.exists(log_path):
        return ['no launch log']
    died = []
    with open(log_path, errors='replace') as log:
        for line in log:
            if re.search(r'user interrupted|signal_handler|sending signal', line):
                break
            # A clean exit mid-run is as fatal as a crash for any node but
            # metrics, which ends the run on purpose.
            match = re.search(
                r'\] \[([\w-]+)\]: process has (?:died|finished cleanly)', line)
            if match and not match.group(1).startswith('metrics_node'):
                died.append(match.group(1))
    return died


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score(run_dir, run_id):
    match = RUN_ID.match(run_id)
    if not match:
        return None
    baseline, event, seed, _ = match.groups()
    row = {'id': run_id, 'kind': 'baseline' if baseline else 'gate', 'event': event,
           'seed': seed or ''}
    if not os.path.isdir(run_dir):
        # Planned but never run: a campaign cut short must not read as a pass.
        return {**row, 'outcome': 'missing', 'early_exits': ''}
    metrics_csv = os.path.join(run_dir, 'metrics.csv')
    if os.path.exists(metrics_csv):
        with open(metrics_csv, newline='') as f:
            rows = list(csv.DictReader(f))
        if rows:
            m = rows[-1]
            row.update({k: m.get(k, '') for k in (
                'outcome', 'lap_time_s', 'max_cte', 'off_track_count', 'cones_hit',
                'max_pos_error')})
            row['invalid_reason'] = m.get('lap_invalid_reason', '')
    row.setdefault('outcome', 'no_metrics')
    if not os.path.exists(os.path.join(run_dir, 'args.txt')):
        # run_gate.sh writes args.txt last, so the run was cut short.
        row['outcome'] = 'incomplete'
    bag = os.path.join(run_dir, 'bag')
    if os.path.isdir(bag):
        try:
            row.update(finish_and_stop(read(bag), event))
        except Exception as error:  # an unreadable bag fails the run, it never crashes the score
            row['invalid_reason'] = f'bag: {error}'
    row['early_exits'] = ';'.join(early_exits(os.path.join(run_dir, 'launch.log')))
    return row


def judge(row, baselines):
    event = row['event']
    stop = number(row.get('stop_dist_m'))
    v_after = number(row.get('max_v_after_stop'))
    cte = number(row.get('max_cte'))
    ekf = number(row.get('max_pos_error'))
    checks = {
        'completes': row['outcome'] == COMPLETE[event],
        'stops': (stop is not None and stop <= STOP_LIMIT_M[event] and bool(row.get('finished'))
                  and v_after is not None and v_after <= MOVED),
        'off_course': row.get('off_track_count') == '0',
        'cones': row.get('cones_hit') == '0',
        'cte': cte is not None and cte <= MAX_CTE_M,
        'ekf': ekf is not None and ekf <= MAX_EKF_ERROR_M,
        'health': not row['early_exits'] and not row.get('emergency'),
    }
    if event == 'autocross':
        lap, base = number(row.get('lap_time_s')), baselines.get(row['seed'])
        row['baseline_lap_s'] = '' if base is None else f'{base:.2f}'
        if lap is not None and base:
            row['lap_ratio'] = f'{lap / base:.2f}'
        checks['lap_time'] = lap is not None and bool(base) and lap <= LAP_TIME_RATIO * base
    else:
        checks['lap_time'] = True  # no lap to compare on a straight
    row.update({k: 'pass' if v else 'FAIL' for k, v in checks.items()})
    row['passed'] = all(checks.values())


def main(out):
    runs = os.path.join(out, 'runs')
    ids = set(os.listdir(runs)) if os.path.isdir(runs) else set()
    expected_path = os.path.join(out, 'expected.txt')
    if os.path.exists(expected_path):
        with open(expected_path) as f:
            ids |= {line.strip() for line in f if line.strip()}
    rows = [r for r in (score(os.path.join(runs, d), d) for d in sorted(ids)) if r]
    baselines = {r['seed']: number(r['lap_time_s']) for r in rows
                 if r['kind'] == 'baseline' and r['outcome'] == 'lap'}
    gate = [r for r in rows if r['kind'] == 'gate']
    for row in gate:
        judge(row, baselines)
    for row in rows:
        for key in ('stop_dist_m', 'stop_time_s', 'max_v_after_stop'):
            if isinstance(row.get(key), float):
                row[key] = f'{row[key]:.2f}'
    with open(os.path.join(out, 'results.csv'), 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    passed = sum(r['passed'] for r in gate)
    lines = [f'# MVP gate: {"PASS" if gate and passed == len(gate) else "FAIL"}, '
             f'{passed} of {len(gate)} runs pass', '',
             '| Run | Outcome | Stop after finish | Off course | Cones | Max CTE | '
             'Max EKF error | Lap / baseline | Health | Pass |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for r in gate:
        stop = f'{r["stop_dist_m"]} m' if r.get('stop_dist_m') else 'never stopped'
        ratio = r.get('lap_ratio') or ('n/a' if r['event'] != 'autocross' else 'no baseline')
        lines.append(
            f'| {r["id"]} | {r["outcome"]} | {stop} ({r["stops"]}) | '
            f'{r.get("off_track_count", "")} ({r["off_course"]}) | '
            f'{r.get("cones_hit", "")} ({r["cones"]}) | {r.get("max_cte", "")} ({r["cte"]}) | '
            f'{r.get("max_pos_error", "")} ({r["ekf"]}) | {ratio} ({r["lap_time"]}) | '
            f'{r["health"]} | {"yes" if r["passed"] else "**no**"} |')
    lines += ['', '## Truth-stack baselines', '', '| Seed | Outcome | Lap time (s) |',
              '| --- | --- | --- |']
    lines += [f'| {r["seed"]} | {r["outcome"]} | {r.get("lap_time_s", "")} |'
              for r in rows if r['kind'] == 'baseline']
    failed = [r for r in gate if not r['passed']]
    if failed:
        lines += ['', '## Failures', '']
        for r in failed:
            why = [c for c in CHECKS if r[c] == 'FAIL']
            extra = '; '.join(x for x in (r.get('invalid_reason'), r['early_exits'] and
                                          f'early exits: {r["early_exits"]}') if x)
            lines.append(f'- {r["id"]}: {", ".join(why)}' + (f' ({extra})' if extra else ''))
    summary = '\n'.join(lines) + '\n'
    with open(os.path.join(out, 'summary.md'), 'w') as f:
        f.write(summary)
    print(summary)
    return 0 if gate and passed == len(gate) else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
