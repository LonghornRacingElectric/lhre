"""Bounded CPU and original FMU timing workloads, executed only inside job containers."""

import argparse
import concurrent.futures
import json
import math
import subprocess
import sys
import time


def cpu(seconds, clock=time.perf_counter):
    start, events = clock(), 0
    while clock() - start < seconds:
        for value in range(2, 12000):
            if all(value % divisor for divisor in range(2, math.isqrt(value) + 1)):
                events += 1
    return {'events': events, 'wall_s': round(clock() - start, 3)}


def fmu(instance):
    result = subprocess.run([sys.executable, '/bench/input/fmu_bench.py', '1e-6',
                             f'worker-{instance}', '/bench/OrionFMI.fmu'],
                            capture_output=True, text=True, timeout=600, check=False)
    print((result.stdout + result.stderr)[-262144:], flush=True)
    records = []
    for line in result.stdout.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    row = next((r for r in reversed(records) if isinstance(r, dict) and r.get('label') == f'worker-{instance}'), {})
    if result.returncode or row.get('error') or row.get('sim_s') != 12:
        raise RuntimeError(f'FMU {instance} did not complete: {row.get("error", result.stderr[-300:])}')
    weights = [('idle', 100), ('accel', 250), ('cruise', 150), ('brake', 100)]
    row['weighted_mean_ms'] = sum(row[phase]['mean_ms'] * count for phase, count in weights) / 600
    return row


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('template', choices=['cpu', 'fmu'])
    parser.add_argument('--parallel', type=int, choices=range(1, 9), default=1)
    parser.add_argument('--seconds', type=int, choices=range(5, 121), default=15)
    args = parser.parse_args()
    started = time.perf_counter()
    print(json.dumps({'event': 'started', 'template': args.template, 'parallel': args.parallel}), flush=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(cpu if args.template == 'cpu' else fmu,
                                [args.seconds] * args.parallel if args.template == 'cpu' else range(1, args.parallel + 1)))
    output = {'template': args.template, 'parallel': args.parallel,
              'wall_s': round(time.perf_counter() - started, 3), 'results': results,
              'note': 'Standalone benchmark, not a full Gazebo/autonomy validation'}
    print(json.dumps(output), flush=True)
