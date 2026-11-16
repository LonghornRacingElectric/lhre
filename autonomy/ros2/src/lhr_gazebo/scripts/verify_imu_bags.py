#!/usr/bin/env python3
"""Independently recompute timestamp-aligned EKF errors from retained gate bags."""

import argparse
import csv
import math
from pathlib import Path

import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message

TOPICS = ('/lhr/vehicle/odom', '/lhr/vehicle/odom_truth')


def poses(bag):
    """Read stamped poses only; do not reuse the metrics accumulator/alignment code."""
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(bag), storage_id='mcap'), ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()
             if t.name in TOPICS}
    data = {topic: [] for topic in TOPICS}
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic not in data:
            continue
        msg = deserialize_message(raw, types[topic])
        q = msg.pose.pose.orientation
        yaw = math.atan2(2 * (q.w*q.z + q.x*q.y), 1 - 2 * (q.y*q.y + q.z*q.z))
        data[topic].append((msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9,
                            msg.pose.pose.position.x, msg.pose.pose.position.y, yaw))
    return [np.asarray(sorted(set(data[topic])), dtype=float) for topic in TOPICS]


def verify(run, campaign):
    """Compare the same scored time window, allowing small delivery/rounding differences."""
    with (run / 'metrics.csv').open(newline='') as stream:
        metrics = list(csv.DictReader(stream))[-1]
    if metrics['estimator'] != 'ekf' or not metrics['max_pos_error']:
        return None
    estimate, truth = poses(run / 'bag')
    start = float(metrics['lap_start_stamp_s'])
    end = start + float(metrics['lap_time_s'] or metrics['duration_s'])
    estimate = estimate[(estimate[:, 0] >= max(start, truth[0, 0]))
                        & (estimate[:, 0] <= min(end, truth[-1, 0]))]
    if not len(estimate):
        raise ValueError(f'{run}: no bracketed estimates in the scored interval')
    interpolated = np.column_stack([
        np.interp(estimate[:, 0], truth[:, 0], values)
        for values in (truth[:, 1], truth[:, 2], np.unwrap(truth[:, 3]))])
    position = np.linalg.norm(estimate[:, 1:3] - interpolated[:, :2], axis=1)
    yaw = estimate[:, 3] - interpolated[:, 2]
    yaw = np.abs(np.arctan2(np.sin(yaw), np.cos(yaw)))
    row = {'campaign': campaign, 'id': run.name, 'samples': len(estimate),
           'start_stamp_s': start, 'end_stamp_s': end}
    passed = True
    for field, measured, tolerance in (
        ('mean_pos_error', np.mean(position), 0.02),
        ('max_pos_error', np.max(position), 0.02),
        ('mean_yaw_error', np.mean(yaw), 0.002),
    ):
        reported = float(metrics[field])
        row.update({f'{field}_reported': reported, f'{field}_bag': float(measured),
                    f'{field}_delta': float(measured - reported)})
        passed = passed and abs(measured - reported) <= tolerance
    row['crosscheck_passed'] = passed
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--campaign', action='append', required=True, metavar='LABEL=PATH')
    args = parser.parse_args()
    rows = []
    for item in args.campaign:
        label, path = item.split('=', 1)
        for run in sorted((Path(path) / 'runs').iterdir()):
            if (run / 'metrics.csv').is_file():
                row = verify(run, label)
                if row:
                    rows.append(row)
    if not rows:
        raise ValueError('No EKF error measurements to cross-check')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    passed = sum(row['crosscheck_passed'] for row in rows)
    print(f'Independent bag cross-check: {passed}/{len(rows)} pass')
    return 0 if passed == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
