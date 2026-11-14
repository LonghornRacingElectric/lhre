#!/usr/bin/env python3
"""Separate stationary temporal jitter from position accuracy in a fixed window."""

import argparse
import json
from pathlib import Path

import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message


def summarize(odom, truth, start, end):
    """Score matched pose timestamps and report temporal spread separately."""
    rows, reference = np.asarray(odom), np.asarray(truth)
    rows = rows[(rows[:, 0] >= start) & (rows[:, 0] <= end)]
    rows = rows[(rows[:, 0] >= reference[0, 0]) & (rows[:, 0] <= reference[-1, 0])]
    if len(rows) < 2 or rows[-1, 0]-rows[0, 0] < .9*(end-start):
        raise ValueError('insufficient fixed-window odometry coverage')
    actual = np.column_stack([np.interp(rows[:, 0], reference[:, 0], reference[:, i])
                              for i in (1, 2)])
    error = np.linalg.norm(rows[:, 1:3]-actual, axis=1)
    scatter = np.linalg.norm(rows[:, 1:3]-rows[:, 1:3].mean(axis=0), axis=1)
    steps = np.linalg.norm(np.diff(rows[:, 1:3], axis=0), axis=1)
    return {
        'window_s': [start, end], 'odom_samples': len(rows),
        'position_error_p95_m': float(np.percentile(error, 95)),
        'position_error_max_m': float(error.max()),
        'temporal_radial_scatter_p95_m': float(np.percentile(scatter, 95)),
        'position_step_p95_m': float(np.percentile(steps, 95)),
        'position_step_max_m': float(steps.max()),
        'odom_interval_max_s': float(np.diff(rows[:, 0]).max()),
        'estimated_abs_speed_p95_m_s': float(np.percentile(abs(rows[:, 3]), 95)),
        'estimated_abs_speed_max_m_s': float(abs(rows[:, 3]).max()),
        'truth_span_xy_m': np.ptp(actual, axis=0).tolist(),
        'truth_abs_speed_max_m_s': float(abs(reference[
            (reference[:, 0] >= start) & (reference[:, 0] <= end), 3]).max()),
    }


def analyze(capture, start, end):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(capture/'bag'), storage_id='mcap'),
                ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    odom, truth, status = [], [], []
    names = ['/lhr/vehicle/odom', '/lhr/vehicle/odom_truth',
             '/lhr/state_estimation/stationary_status']
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic not in names:
            continue
        msg = deserialize_message(raw, types[topic])
        if topic == names[2]:
            status.append(json.loads(msg.data))
            continue
        p = msg.pose.pose.position
        row = [msg.header.stamp.sec+msg.header.stamp.nanosec*1e-9,
               p.x, p.y, msg.twist.twist.linear.x]
        (odom if topic == names[0] else truth).append(row)
    result = summarize(odom, truth, start, end)
    applied = [s['stamp_s'] for s in status if s['applied']]
    truth = np.asarray(truth)
    measured = [t for t in applied if truth[0, 0] <= t <= truth[-1, 0]]
    result['max_truth_abs_speed_at_stationary_correction_m_s'] = (
        float(np.max(abs(np.interp(measured, truth[:, 0], truth[:, 3])))) if measured else None)
    result.update(first_stationary_correction_s=min(applied) if applied else None,
                  last_stationary_correction_s=max(applied) if applied else None,
                  stationary_corrections_in_window=sum(start <= t <= end for t in applied))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--start', type=float, default=9.)
    parser.add_argument('--end', type=float, default=20.)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture, args.start, args.end), indent=2))


if __name__ == '__main__':
    main()
