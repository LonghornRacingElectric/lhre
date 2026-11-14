#!/usr/bin/env python3
"""Replay physics IMUs through wheel-free dead reckoning; truth is evaluation only."""

import argparse
import csv
import json
import math
from pathlib import Path

from lhr_state_estimation.ekf import VehicleEkf, wrap_angle
from lhr_vehicle import load_vehicle
import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message


def read_capture(path):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(path / 'bag'), storage_id='mcap'), ConverterOptions('', ''))
    types = {topic.name: get_message(topic.type) for topic in reader.get_all_topics_and_types()}
    imu, truth, clock, states = [], [], [], []
    while reader.has_next():
        topic, data, received = reader.read_next()
        msg = deserialize_message(data, types[topic])
        if topic == '/clock':
            clock.append((received * 1e-9, msg.clock.sec + msg.clock.nanosec * 1e-9))
        elif topic == '/lhr/mission/status':
            states.append((received * 1e-9, msg.data))
        elif topic in ('/lhr/imu/data', '/lhr/vehicle/odom'):
            stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
            if topic == '/lhr/imu/data':
                # Deliberately ignore the simulator's perfect orientation quaternion.
                imu.append((stamp, msg.angular_velocity.x, msg.angular_velocity.y,
                            msg.angular_velocity.z, msg.linear_acceleration.x,
                            msg.linear_acceleration.y, msg.linear_acceleration.z))
            else:
                p, q = msg.pose.pose.position, msg.pose.pose.orientation
                yaw = math.atan2(2*(q.w*q.z + q.x*q.y), 1 - 2*(q.y*q.y + q.z*q.z))
                truth.append((stamp, p.x, p.y, yaw, msg.twist.twist.linear.x))
    imu = np.asarray(sorted(set(imu)))
    truth = np.asarray(sorted(set(truth)))
    clock = np.asarray(clock)
    phases = [(float(np.interp(t, clock[:, 0], clock[:, 1])), state) for t, state in states]
    return imu, truth, phases


def evaluate(path, out):
    imu, truth, phases = read_capture(path)
    start = imu[0, 0] + 2.0
    end = start + 5.0
    drive = next(t for t, state in phases if state == 'DRIVING')
    finish = next(t for t, state in phases if state == 'FINISHED')
    if drive <= end:
        raise ValueError('No five-second declared stationary interval before DRIVING')
    calibration = imu[(imu[:, 0] >= start) & (imu[:, 0] <= end)]
    if len(calibration) < 400:
        raise ValueError('Insufficient stationary IMU samples')
    means = np.mean(calibration[:, 1:], axis=0)
    sigma = np.std(calibration[:, 1:], axis=0, ddof=1)
    # Score the operator's held-still contract independently; never use truth to
    # compute calibration offsets or to select a deceptively good sample window.
    held = truth[(truth[:, 0] >= start) & (truth[:, 0] <= end)]
    if np.ptp(held[:, 1:3], axis=0).max() > 0.001 or np.max(abs(held[:, 4])) > 0.001:
        raise ValueError('Reference was not held stationary during calibration')
    (out / f'{path.name}-calibration.json').write_text(json.dumps({
        'start_s': float(start), 'end_s': float(end), 'samples': len(calibration),
        'gyro_xyz_mean_rad_s': means[:3].tolist(),
        'accel_xyz_mean_m_s2': means[3:].tolist(), 'sample_stddev': sigma.tolist(),
        'forward_offset_assumption': 'known-level, stationary, fixed planar mount',
    }, indent=2) + '\n')
    active = imu[(imu[:, 0] >= end) & (imu[:, 0] <= finish)]
    initial = [float(np.interp(active[0, 0], truth[:, 0], values))
               for values in (truth[:, 1], truth[:, 2], np.unwrap(truth[:, 3]))]
    summaries = []
    lever_arm_x = load_vehicle().chassis_center_x_m
    for mode in ('raw', 'gyro_calibrated', 'gyro_accel_calibrated', 'lever_arm_diagnostic'):
        ekf = VehicleEkf()
        ekf.reset(*initial)  # One surveyed-start-pose proxy; initial speed is known zero.
        gyro_bias = means[2] if mode != 'raw' else 0.0
        accel_bias = means[3] if mode in ('gyro_accel_calibrated', 'lever_arm_diagnostic') else 0.0
        rows = []
        previous = active[0, 0]
        for stamp, gx, gy, gz, ax, ay, az in active:
            dt = stamp - previous
            if dt < 0 or dt > 0.1:
                raise ValueError('IMU gap exceeds 0.1 s; do not hide a delivery failure')
            previous = stamp
            rate = gz - gyro_bias
            accel = ax - accel_bias
            if mode == 'lever_arm_diagnostic':
                # Gazebo's IMU is forward of base_link. This is a planar rigid-body
                # correction from measured rate and canonical mount geometry.
                accel += rate**2 * lever_arm_x
            ekf.update_yaw_rate(rate, 0.0004)
            ekf.predict(dt, accel)
            if stamp < drive:
                continue
            px, py, yaw = ekf.pose
            reference = [float(np.interp(stamp, truth[:, 0], values))
                         for values in (truth[:, 1], truth[:, 2], np.unwrap(truth[:, 3]),
                                        truth[:, 4])]
            error = math.hypot(px-reference[0], py-reference[1])
            rows.append((stamp, px, py, yaw, ekf.twist[0], error,
                         abs(wrap_angle(yaw-reference[2])), ekf.twist[0]-reference[3]))
        values = np.asarray(rows)
        with (out / f'{path.name}-{mode}.csv').open('w') as stream:
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(['stamp_s', 'x', 'y', 'yaw', 'speed', 'pos_error_m',
                             'yaw_error_rad', 'speed_error_m_s'])
            writer.writerows(rows)
        summaries.append({
            'capture': path.name, 'mode': mode, 'samples': len(rows),
            'scored_duration_s': float(values[-1, 0]-values[0, 0]),
            'mean_pos_error_m': float(np.mean(values[:, 5])),
            'max_pos_error_m': float(np.max(values[:, 5])),
            'final_pos_error_m': float(values[-1, 5]),
            'mean_yaw_error_rad': float(np.mean(values[:, 6])),
            'max_speed_error_m_s': float(np.max(abs(values[:, 7]))),
            'within_0_5m': bool(np.max(values[:, 5]) <= 0.5),
        })
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('captures', nargs='+', type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for capture in args.captures:
        rows.extend(evaluate(capture, args.out))
    with (args.out / 'summary.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(f"{row['capture']:24} {row['mode']:24} "
              f"max={row['max_pos_error_m']:.4f} m", flush=True)


if __name__ == '__main__':
    main()
