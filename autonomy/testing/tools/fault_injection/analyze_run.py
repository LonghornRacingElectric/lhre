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
from pathlib import Path
import sys

from lhr_safety.monitors import SafetyConfig
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message
from ros2launch.api.api import parse_launch_arguments

# The PDR stop formula uses the allowed detection time T, the brake apply
# time, and the spring brake's minimum deceleration.
APPLY_S = 0.2
DECEL = 6.0
TRIP_LIMITS = {
    'EKF_DIVERGED': .2,
    'CMD_INVALID': .2,
    'GEOFENCE': .2,
    'STEER_TRACKING': 1.,
}
STALE_TIMERS = {
    'ODOM_STALE': 'odom_timeout_s',
    'CMD_STALE': 'cmd_timeout_s',
    'LIDAR_STALE': 'lidar_timeout_s',
    'IMU_STALE': 'imu_timeout_s',
    'WHEEL_STALE': 'wheel_timeout_s',
}
STOPPED = 0.1  # m/s
MOVED = 0.5  # m/s: rolling again after the stop fails the run
MIN_DRIVE_M = 10.0  # less than this before the fault and the run proves nothing
# A fault below this share of v_max did not test its tier: the 11/1 oval
# runs labelled 8 m/s faulted at 5.3 (#207).
AT_SPEED = 0.9
# A frozen actuator is invisible while the command still asks for the
# frozen angle; STEER_TRACKING's budget runs from when the two differ by
# its tolerance (#208).
STEER_TOL_RAD = SafetyConfig.steer_angle_tol_rad

FIELDS = [
    'id', 'case', 'track', 'seed', 'v_max', 'result', 'expected', 'tripped',
    'match', 'v_at_fault', 'at_speed', 'trip_latency_s', 'trip_limit_s',
    'within_trip_limit', 'emergency_latency_s',
    'stop_time_s', 'stop_dist_m', 'pdr_dist_m', 'within_pdr',
    'max_v_after_stop', 'false_trip', 'drove_m', 'visible_after_s', 'latency_from_visible_s',
    'passed',
]

# result: what the run shows. Only the first four (and the two clean ones)
# are evidence; the rest say the run itself went wrong.
#   caught       the expected check tripped after the fault
#   wrong_check  another check tripped after the fault
#   missed       the fault ran and nothing tripped
#   early_trip   something tripped before the fault: a false trip
#   clean        a clean lap that drove and never tripped
#   false_trip   a clean lap that tripped
#   incomplete_lap  a run that drove without proving a completed clean lap
#   no_drive     never reached DRIVING, or barely moved before the fault
#   no_fault     drove, but the fault never started: RUN_S ran out, or the
#                car never reached the speed gate (the launch log says so)
#   hung         the sim clock stopped (watch_run.py), so the run proves nothing
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


def trip_limits(launch_args):
    """Use the same duplicate-argument precedence as ros2 launch."""
    args = dict(parse_launch_arguments(launch_args))
    limits = TRIP_LIMITS.copy()
    for check, parameter in STALE_TIMERS.items():
        name = f'safety_{parameter}'
        timeout = float(args.get(name, getattr(SafetyConfig, parameter)))
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError(f'{name} must be finite and nonnegative')
        limits[check] = timeout + .2
    return limits


def score(data, case, fence, launch_args=()):
    """Score one run from {topic: [(receive time, msg)]}."""
    limits = trip_limits(launch_args)
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
        lap = next((to_sim(t) for t, m in data.get('/lhr/metrics/lap_complete', [])
                    if m.data and to_sim(t) >= driving), None)
        finished = lap is not None and any(s == 'FINISHED' and t >= lap for t, s in mission)
        if trip or emergency is not None:
            result = 'false_trip'
        elif drove < MIN_DRIVE_M:
            result = 'no_drive'
        else:
            result = 'clean' if finished and data.get('/lhr/safety/status') else 'incomplete_lap'
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
    if fault_t is not None and result in ('caught', 'wrong_check', 'missed'):
        # A missed fault still says which tier it tested.
        v = next((s for t, _, _, s in reversed(truth) if t <= fault_t), math.nan)
        row['v_at_fault'] = f'{v:.2f}'
    if result not in ('caught', 'wrong_check') or fault_t is None:
        return row

    latency = trip[0] - fault_t
    detection = latency
    if case == 'steer_freeze':
        # The injector freezes the first command after the fault starts.
        steer = [(to_sim(t), m.drive.steering_angle) for t, m in data.get('/lhr/vehicle/cmd', [])]
        frozen = next((a for t, a in steer if t >= fault_t), None)
        visible = next((t for t, a in steer if t >= fault_t and frozen is not None
                        and abs(a - frozen) > STEER_TOL_RAD), None)
        # A trip before the freeze shows (or one that never shows) beat the
        # budget outright: 0, never negative and never blank.
        row['visible_after_s'] = 'never' if visible is None else f'{visible - fault_t:.3f}'
        after = 0.0 if visible is None else max(0.0, trip[0] - visible)
        row['latency_from_visible_s'] = f'{after:.3f}'
        detection = math.inf if visible is None else after
    stop = next((t for t, _, _, s in truth if t > trip[0] and s < STOPPED), None)
    limit = limits.get(expected)
    row.update({
        'trip_latency_s': f'{latency:.3f}',
        'emergency_latency_s': (f'{emergency - trip[0]:.3f}'
                                if emergency is not None else ''),
        'within_trip_limit': str(limit is not None and detection <= limit),
        'within_pdr': 'False',
    })
    if limit is None or not math.isfinite(v):
        return row
    pdr = v * (limit + APPLY_S) + v * v / (2 * DECEL)
    row.update(trip_limit_s=f'{limit:.3f}', pdr_dist_m=f'{pdr:.2f}')
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


def at_speed(v_at_fault, v_max):
    """Return 'True' if the fault hit at its tier's speed, '' if either is unknown."""
    try:
        v, top = float(v_at_fault), float(v_max)
    except (TypeError, ValueError):
        return ''
    if not (math.isfinite(v) and math.isfinite(top) and top > 0.0):
        return ''
    return str(v >= AT_SPEED * top)


def passed(row):
    """Require completion or all three fault checks on the same run."""
    if row['case'] == 'clean':
        return row['result'] == 'clean'
    if not (row['result'] == 'caught' and row.get('within_trip_limit') == 'True'
            and row.get('within_pdr') == 'True'):
        return False
    if row['case'] == 'geofence':
        return True
    if row['at_speed'] != 'True':
        return False
    return float(row['v_max']) != 8. or float(row['v_at_fault']) >= 7.5


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
    ap.add_argument('--end', default='', help="watch_run.py's end file")
    ap.add_argument('--launch-args', type=Path, help='recorded launch arguments, one per line')
    ap.add_argument('--csv', required=True)
    args = ap.parse_args()

    status = 0
    end = ''
    if args.end and os.path.exists(args.end):
        with open(args.end) as f:
            end = f.read()
    try:
        launch_args = args.launch_args.read_text().splitlines() if args.launch_args else ()
        row = score(read(args.bag), args.case, args.fence, launch_args=launch_args)
    except Exception as e:  # a bad bag still gets a row, so it is not lost
        print(f'analyze_run: {args.bag}: {e}', file=sys.stderr)
        row, status = {'case': args.case, 'result': 'error'}, 1
    reason = next((word.split('=', 1)[1] for word in end.split()
                   if word.startswith('reason=')), '')
    if reason in ('hung', 'exited', 'interrupted'):
        # Whatever the bag shows stopped with the sim, not with the car.
        row = {'case': args.case, 'result': reason}
    # The geofence case trips where the car crosses the fence, not at a
    # tier's speed, so at_speed says nothing about it.
    tier = '' if args.case == 'geofence' else at_speed(row.get('v_at_fault'), args.v_max)
    row.update({'id': args.id, 'track': args.track, 'seed': args.seed,
                'v_max': args.v_max, 'at_speed': tier})
    row['passed'] = str(passed(row))
    save(args.csv, row)
    print('  '.join(f'{k}={row[k]}' for k in FIELDS if row.get(k)))
    return status


if __name__ == '__main__':
    sys.exit(main())
