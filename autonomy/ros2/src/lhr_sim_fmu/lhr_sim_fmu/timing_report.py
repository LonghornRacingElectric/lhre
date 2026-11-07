"""Collect accepted FMU step costs and observed sensor pose ages on the same run."""

import argparse
import json
import math
from pathlib import Path
import time

LIDAR = '/lhr/lidar/points'
CAMERAS = tuple(f'/lhr/sensor/zed2i/{side}/image_rect' for side in ('left', 'right'))


def distribution(values):
    """Use a nearest-rank percentile, including the slowest accepted step."""
    values = sorted(values)
    if not values:
        return {'count': 0, 'mean': None, 'p95': None, 'min': None, 'max': None}
    return {'count': len(values), 'mean': sum(values)/len(values),
            'p95': values[math.ceil(.95*len(values))-1], 'min': values[0], 'max': values[-1]}


def summarize(steps, poses, *, stereo=False):
    """Fail missing streams, unmeasured speeds, unmatched poses or frame-age violations."""
    groups = {}
    for name, rows in (('all', steps), ('speed_4_to_5_mps', [
            r for r in steps if 4. <= r['speed_mps'] <= 5.])):
        wall = sum(r['wall_sec'] for r in rows)
        simulated = sum(r['step_sec'] for r in rows)
        groups[name] = {
            'wall_sec': distribution([r['wall_sec'] for r in rows]),
            'simulated_sec': simulated,
            'plant_speedup': simulated/wall if wall else None,
            'overruns': sum(r['wall_sec'] > r['step_sec'] for r in rows),
        }
    origins = {r.get('clock_origin_sec') for r in steps}
    origin = next(iter(origins)) if len(origins) == 1 and None not in origins else None
    start = origin+min(r['fmu_time_sec'] for r in steps) if origin is not None else None
    sensors = {}
    for topic in (LIDAR, *CAMERAS) if stereo else (LIDAR,):
        stream = [r for r in poses if r['topic'] == topic]
        rows = [r for r in stream if r['sensor_stamp_sec'] > 0
                and (start is None or r['sensor_stamp_sec'] >= start)]
        matched = [r for r in rows if r['matched']]
        stats = distribution([r['lag_sec'] for r in matched])
        stats['pre_start'] = len(stream)-len(rows)
        stats['unmatched'] = len(rows)-len(matched)
        stats['observation_age_sec'] = distribution([r['observation_age_sec'] for r in matched])
        sensors[topic] = stats
    passed = origin is not None and bool(groups['speed_4_to_5_mps']['wall_sec']['count']) and all(
        s['count'] > 0 and s['unmatched'] == 0 and 0 <= s['min'] <= s['max'] < .1
        for s in sensors.values())
    return {'steps': groups, 'sensors': sensors, 'timing_passed': passed,
            'lockstep_modes': sorted({r['lockstep'] for r in poses}),
            'clock_origin_sec': origin, 'measurement_start_sec': start}


def main():
    """Observe wall time independently of the paused simulation clock."""
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile
    from std_msgs.msg import String

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=180.)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stereo', action='store_true')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error('--seconds must be finite and positive')
    rclpy.init()
    node = Node('fmu_timing_report')
    steps, poses, provenance = [], [], []
    arrival = []

    def step(message):
        row = json.loads(message.data)
        if not all(math.isfinite(row[k]) for k in ('wall_sec', 'step_sec', 'speed_mps')):
            raise ValueError('Nonfinite FMU timing sample')
        steps.append(row)
        arrival.append(time.monotonic())

    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(String, '/lhr/sim/provenance',
                             lambda msg: provenance.append(json.loads(msg.data)), qos)
    node.create_subscription(String, '/lhr/sim/fmu_step_timing', step, 1000)
    node.create_subscription(String, '/lhr/sim/fmu_pose_lag',
                             lambda msg: poses.append(json.loads(msg.data)), 1000)
    deadline = time.monotonic()+args.seconds
    try:
        while rclpy.ok() and time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=.1)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    report = summarize(steps, poses, stereo=args.stereo)
    report['provenance'] = provenance
    report['timing_passed'] = report['timing_passed'] and bool(provenance)
    report['observed_real_time_factor'] = (
        (steps[-1]['fmu_time_sec']-steps[0]['fmu_time_sec'])/(arrival[-1]-arrival[0])
        if len(steps) > 1 else None)
    report['step_samples'] = steps
    report['pose_samples'] = poses
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if not k.endswith('_samples')}, indent=2))
    return 0 if report['timing_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
