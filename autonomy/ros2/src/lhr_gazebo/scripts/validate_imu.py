#!/usr/bin/env python3
"""Measure the actual bridged vehicle IMU while auto_go is disabled."""

import argparse
import csv
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time

from lhr_vehicle import load_vehicle, vehicle_sha256
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu


def measure(out, label, enabled, bias, seconds):
    """Launch one stationary vehicle and retain stamped six-axis samples."""
    rows = []
    node = Node('imu_validation')
    first_stamp = None

    def receive(msg):
        nonlocal first_stamp
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if first_stamp is None:
            first_stamp = stamp
        if stamp - first_stamp < 5:
            return
        rows.append([stamp, msg.angular_velocity.x, msg.angular_velocity.y,
                     msg.angular_velocity.z, msg.linear_acceleration.x,
                     msg.linear_acceleration.y, msg.linear_acceleration.z])

    node.create_subscription(Imu, '/lhr/imu/data', receive, qos_profile_sensor_data)
    args = ['ros2', 'launch', 'lhr_gazebo', 'gazebo_demo.launch.py',
            'gui:=false', 'rviz:=false', 'auto_go:=false', 'enable_metrics:=false',
            'perception:=sim', 'estimator:=ekf', 'seed:=1',
            f'imu_noise:={str(enabled).lower()}', f'gyro_bias:={bias}']
    (out / f'{label}-args.json').write_text(json.dumps(args, indent=2) + '\n')
    with (out / f'{label}-launch.log').open('w') as log:
        proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
                if rows and rows[-1][0] - rows[0][0] >= seconds:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f'{label}: launch exited; see {log.name}')
            else:
                raise RuntimeError(f'{label}: timed out; see {log.name}')
        finally:
            node.destroy_node()
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            except ProcessLookupError:
                pass
    fields = ['stamp_s', 'gx', 'gy', 'gz', 'ax', 'ay', 'az']
    with (out / f'{label}-samples.csv').open('w') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(fields)
        writer.writerows(rows)
    summary = {'label': label, 'imu_noise': enabled, 'gyro_bias': bias,
               'samples': len(rows), 'duration_s': rows[-1][0] - rows[0][0],
               'vehicle_sha256': vehicle_sha256()}
    for i, axis in enumerate(fields[1:], 1):
        summary[f'{axis}_mean'] = statistics.mean(row[i] for row in rows)
        summary[f'{axis}_stddev'] = statistics.stdev(row[i] for row in rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--seconds', type=float, default=15)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    args.out.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    try:
        results = [measure(args.out, label, enabled, bias, args.seconds)
                   for label, enabled, bias in (
                       ('clean', False, 0.0), ('native', True, 0.0),
                       ('positive_bias', False, 0.03), ('negative_bias', False, -0.03))]
    finally:
        rclpy.shutdown()
    with (args.out / 'stationary.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(results)
    clean, native, positive, negative = results
    imu = load_vehicle().imu
    checks = {
        'sample_rate': all(abs(r['samples'] / r['duration_s'] - imu.update_rate_hz) < 2
                           for r in results),
        'positive_bias': abs(positive['gz_mean'] - clean['gz_mean'] - 0.03) < 0.001,
        'negative_bias': abs(negative['gz_mean'] - clean['gz_mean'] + 0.03) < 0.001,
        'clean_gyro': all(clean[f'g{a}_stddev'] < imu.gyro_stddev_rad_s / 10 for a in 'xyz'),
        'native_gyro_noise': all(
            0.8 < native[f'g{a}_stddev'] / imu.gyro_stddev_rad_s < 1.2 for a in 'xyz'),
        'native_accel_noise': all(
            0.8 < native[f'a{a}_stddev'] / imu.accel_stddev_m_s2 < 1.2 for a in 'xyz'),
        'startup_bias_present': any(
            abs(native[f'g{a}_mean'] - clean[f'g{a}_mean']) > imu.gyro_stddev_rad_s
            for a in 'xyz'),
        'startup_accel_bias_present': any(
            abs(native[f'a{a}_mean'] - clean[f'a{a}_mean']) > imu.accel_stddev_m_s2
            for a in 'xyz'),
    }
    (args.out / 'checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(json.dumps(checks, indent=2), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
