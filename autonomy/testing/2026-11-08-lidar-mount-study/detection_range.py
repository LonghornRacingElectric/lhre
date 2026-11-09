#!/usr/bin/env python3
"""
How far ahead the cone map first holds each cone, for each arm of the sim A/B.

    python3 detection_range.py <campaign>/inv7 <campaign>/inv6 ... > detection_range.csv

Each arm is a run_gate.sh OUT folder recorded with /lhr/sensor/cones_detected
added to its TOPICS. The detector's map only grows, and marker i is map
entry i, so a cone enters the map the first time its marker id appears. That
cone counts when it lands within MATCH_M of a world cone, and its distance is
measured along the car from the nose at that moment. Cones that appear while
the car is still within START_M of where it started are left out: they are
the start box, which every arm sees from standstill.

Run it where the bags are, with ROS sourced (rosbag2_py, lhr_lidar_sim).
"""

import csv
import math
from pathlib import Path
import re
import sys

from lhr_lidar_sim.world import load_world_cones
import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message

MATCH_M = 0.5
START_M = 10.0
NOSE_X = 0.955 + 1.5494   # orion.stl nose tip in base_link (rear axle at ground)
WORLDS = Path(__file__).resolve().parents[2] / 'ros2/src/lhr_gazebo/worlds'
TOPICS = ('/lhr/sensor/cones_detected', '/lhr/vehicle/odom_truth')


def read(bag):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(bag), storage_id='mcap'), ConverterOptions('', ''))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    out = {topic: [] for topic in TOPICS}
    while reader.has_next():
        topic, data, t = reader.read_next()
        if topic in out:
            out[topic].append((t * 1e-9, deserialize_message(data, get_message(types[topic]))))
    return out


def world_for(arm, run_id):
    seed = re.search(r'_s(\d+)', run_id)
    if run_id.startswith('rules_autocross'):
        return arm / 'worlds' / f'rules_autocross_seed{seed.group(1)}.sdf'
    if run_id.startswith('acceleration'):
        return WORLDS / 'acceleration_seed1.sdf'
    return WORLDS / f'autocross_seed{seed.group(1)}.sdf'


def first_sightings(topics, cones):
    """Return (forward past the nose, lateral) at each world cone's first sighting, and misses."""
    odom = topics['/lhr/vehicle/odom_truth']
    if not odom:
        return [], 0
    ot = np.array([t for t, _ in odom])
    pose = np.array([(m.pose.pose.position.x, m.pose.pose.position.y,
                      2 * math.atan2(m.pose.pose.orientation.z, m.pose.pose.orientation.w))
                     for _, m in odom])
    start = pose[0, :2]
    seen_ids, first = set(), {}
    false_cones = 0
    for t, msg in topics['/lhr/sensor/cones_detected']:
        for marker in msg.markers:
            if marker.action != 0 or marker.id in seen_ids:
                continue
            seen_ids.add(marker.id)
            x, y, yaw = pose[min(np.searchsorted(ot, t), len(ot) - 1)]
            if np.hypot(x - start[0], y - start[1]) < START_M:
                continue
            p = np.array([marker.pose.position.x, marker.pose.position.y])
            d = np.hypot(*(cones[:, :2] - p).T)
            k = int(np.argmin(d))
            if d[k] > MATCH_M:
                false_cones += 1
                continue
            if k in first:
                continue
            dx, dy = cones[k, 0] - x, cones[k, 1] - y
            first[k] = (dx * math.cos(yaw) + dy * math.sin(yaw) - NOSE_X,
                        -dx * math.sin(yaw) + dy * math.cos(yaw))
    return list(first.values()), false_cones


def main():
    rows = []
    for arm in map(Path, sys.argv[1:]):
        gate = {}
        if (arm / 'results.csv').exists():
            gate = {r['id']: r for r in csv.DictReader(open(arm / 'results.csv'))}
        for run in sorted((arm / 'runs').iterdir()):
            if not (run / 'bag').exists():
                continue
            cones = load_world_cones(world_for(arm, run.name))
            sightings, false_cones = first_sightings(read(run / 'bag'), cones)
            fwd = np.array([f for f, _ in sightings]) if sightings else np.array([np.nan])
            g = gate.get(run.name, {})
            rows.append({
                'arm': arm.name, 'run': run.name, 'cones_seen': len(sightings),
                'false_cones': false_cones,
                'first_seen_median_m': np.nanmedian(fwd),
                'first_seen_p10_m': np.nanpercentile(fwd, 10),
                'seen_by_8m': np.mean(fwd >= 8.0), 'seen_by_12m': np.mean(fwd >= 12.0),
                'passed': g.get('passed', ''), 'lap_ratio': g.get('lap_ratio', ''),
                'max_cte': g.get('max_cte', ''), 'cones_hit': g.get('cones_hit', ''),
            })
    writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: f'{v:.2f}' if isinstance(v, float) else v for k, v in row.items()})


if __name__ == '__main__':
    main()
