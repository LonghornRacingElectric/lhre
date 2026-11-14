#!/usr/bin/env python3
"""Compare physical and estimated motion during a recorded wheel-free stop."""

import argparse
import csv
import json
from pathlib import Path

from lhr_vehicle import load_vehicle
import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message


def rotations(quaternions):
    """Body-to-reference rotation matrices for xyzw quaternion rows."""
    q = quaternions/np.linalg.norm(quaternions, axis=1)[:, None]
    x, y, z, w = q.T
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ]).transpose(2, 0, 1)


def analyze_probe(path, low):
    """Use ideal attitude and 3D motion only to diagnose sampling, never for fusion."""
    high = np.loadtxt(path / 'probe-imu.csv', delimiter=',', skiprows=1)
    odom = np.loadtxt(path / 'probe-odom3d.csv', delimiter=',', skiprows=1)
    if min(high[-1, 0], odom[-1, 0], low[-1, 0]) < 29:
        raise ValueError('The fixed probe windows require recording through 29 simulation seconds')
    ro, rh, rl = rotations(odom[:, 4:8]), rotations(high[:, 7:11]), rotations(low[:, 8:12])
    # Gazebo's IMU orientation is relative to its initial heading. Align it
    # once to the independent world attitude for this diagnostic integration.
    alignment = ro[np.argmin(abs(odom[:, 0]-3))] @ rh[np.argmin(abs(high[:, 0]-3))].T
    rh, rl = alignment @ rh, alignment @ rl
    vehicle = load_vehicle()
    lever = np.array([vehicle.chassis_center_x_m, 0, vehicle.chassis_z_m])
    sensor_position = odom[:, 1:4] + np.einsum('nij,j->ni', ro, lever)
    reference_velocity = np.gradient(sensor_position, odom[:, 0], axis=0)
    held = (low[:, 0] >= 2) & (low[:, 0] <= 4)
    gravity = np.einsum('nji,j->ni', rl[held], np.array([0, 0, 9.81])).mean(axis=0)
    bias = low[held, 4:7].mean(axis=0)-gravity
    high_world = np.einsum('nij,nj->ni', rh, high[:, 4:7]) + [0, 0, -9.81]
    low_world = np.einsum('nij,nj->ni', rl, low[:, 4:7]-bias) + [0, 0, -9.81]
    # Sample the noiseless 1 kHz probe at the production sensor timestamps.
    # This separates temporal sampling from the production sensor's noise.
    downsampled = np.column_stack([
        np.interp(low[:, 0], high[:, 0], high_world[:, k]) for k in range(3)])
    windows = []
    for begin, end in [(8, 14), (14, 16.3), (8, 16.3), (17, 29)]:
        expected = np.array([
            np.interp(end, odom[:, 0], reference_velocity[:, k])
            - np.interp(begin, odom[:, 0], reference_velocity[:, k]) for k in range(3)])
        result = {'begin_s': begin, 'end_s': end,
                  'sensor_world_delta_v_from_position': expected.tolist()}
        for name, accel, stamps in [('probe_1000hz', high_world, high[:, 0]),
                                    ('primary', low_world, low[:, 0]),
                                    ('probe_at_primary_timestamps', downsampled, low[:, 0])]:
            times = np.r_[begin, stamps[(stamps > begin) & (stamps < end)], end]
            measured = np.array([np.trapz(np.interp(times, stamps, accel[:, k]), times)
                                 for k in range(3)])
            result[name] = {'world_delta_v_from_imu': measured.tolist(),
                            'vector_error_m_s': float(np.linalg.norm(measured-expected))}
        windows.append(result)
    return {'offline_bias_m_s2': bias.tolist(),
            'primary_median_dt_s': float(np.median(np.diff(low[:, 0]))), 'windows': windows}


def read(path):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(path / 'bag'), storage_id='mcap'), ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    values = {key: [] for key in ('imu', 'truth', 'estimate', 'cmd', 'clock', 'status')}
    while reader.has_next():
        topic, data, received = reader.read_next()
        msg = deserialize_message(data, types[topic])
        if topic == '/clock':
            values['clock'].append((received*1e-9, msg.clock.sec + msg.clock.nanosec*1e-9))
        elif topic == '/lhr/mission/status':
            values['status'].append((received*1e-9, msg.data))
        elif topic in ('/lhr/vehicle/odom', '/lhr/vehicle/odom_truth', '/lhr/imu/data',
                       '/lhr/vehicle/cmd'):
            stamp = msg.header.stamp.sec + msg.header.stamp.nanosec*1e-9
            if topic == '/lhr/imu/data':
                g, a, q = msg.angular_velocity, msg.linear_acceleration, msg.orientation
                # Ideal simulator attitude is diagnostic only, never fed to the EKF.
                gravity_x = 9.81*2*(q.x*q.z-q.w*q.y)
                values['imu'].append((stamp, g.x, g.y, g.z, a.x, a.y, a.z, gravity_x,
                                      q.x, q.y, q.z, q.w))
            elif topic == '/lhr/vehicle/cmd':
                values['cmd'].append((stamp, msg.drive.speed, msg.drive.steering_angle))
            else:
                p, v = msg.pose.pose.position, msg.twist.twist.linear
                key = 'truth' if topic.endswith('_truth') else 'estimate'
                values[key].append((stamp, p.x, p.y, v.x, v.y))
    for key in ('imu', 'truth', 'estimate', 'cmd', 'clock'):
        values[key] = np.asarray(sorted(set(values[key])))
    return values


def analyze(path, out):
    """Describe stationary intervals without applying truth-based filter corrections."""
    data = read(path)
    imu, truth, estimate = [data[key] for key in ('imu', 'truth', 'estimate')]
    first_motion = truth[np.hypot(truth[:, 3], truth[:, 4]) > 0.3, 0]
    intervals = [('initial_held', 2.0, 4.0)]
    # Truth selects an evaluation interval only. No detector/control/filter
    # changes use this information.
    if len(first_motion):
        stopped = truth[(truth[:, 0] > first_motion[0]+1)
                        & (np.hypot(truth[:, 3], truth[:, 4]) < 0.01)]
        if len(stopped):
            groups = np.split(stopped, np.flatnonzero(np.diff(stopped[:, 0]) > 0.05)+1)
            groups = [g for g in groups if g[-1, 0]-g[0, 0] >= 2]
            if groups:
                group = max(groups, key=lambda g: g[-1, 0]-g[0, 0])
                intervals.append(('physical_stop', float(group[0, 0]), float(group[-1, 0])))
    results = []
    startup = imu[(imu[:, 0] >= 2) & (imu[:, 0] <= 4)]
    for name, begin, end in intervals:
        p = truth[(truth[:, 0] >= begin) & (truth[:, 0] <= end)]
        s = imu[(imu[:, 0] >= begin) & (imu[:, 0] <= end)]
        e = estimate[(estimate[:, 0] >= begin) & (estimate[:, 0] <= end)]
        cmd = data['cmd'][(data['cmd'][:, 0] >= begin) & (data['cmd'][:, 0] <= end)]
        if min(len(p), len(s), len(e)) < 2:
            raise ValueError(f'Insufficient samples in {name}')
        estimate_travel = np.linalg.norm(np.diff(e[:, 1:3], axis=0), axis=1).sum()
        results.append({
            'interval': name, 'begin_s': begin, 'end_s': end,
            'truth_position_span_m': float(np.linalg.norm(np.ptp(p[:, 1:3], axis=0))),
            'truth_max_speed_m_s': float(np.max(np.hypot(p[:, 3], p[:, 4]))),
            'estimate_speed_begin_m_s': float(e[0, 3]),
            'estimate_speed_end_m_s': float(e[-1, 3]),
            'estimate_position_travel_m': float(estimate_travel),
            'imu_ax_mean_m_s2': float(s[:, 4].mean()),
            'imu_ax_std_m_s2': float(s[:, 4].std(ddof=1)),
            'imu_ax_minus_startup_mean_m_s2': float(s[:, 4].mean()-startup[:, 4].mean()),
            'diagnostic_gravity_x_mean_m_s2': float(s[:, 7].mean()),
            'max_abs_command_speed_m_s': float(np.max(abs(cmd[:, 1]))) if len(cmd) else None,
        })
    native = np.loadtxt(path / 'native-imu.csv', delimiter=',', skiprows=1)
    native_by_stamp = {round(row[0], 9): row[1:] for row in native}
    pairs = [(row[1:7], native_by_stamp[round(row[0], 9)]) for row in imu
             if round(row[0], 9) in native_by_stamp]
    differences = np.asarray(pairs)
    clock = data['clock']
    status_changes = []
    for received, state in data['status']:
        if not status_changes or status_changes[-1]['state'] != state:
            status_changes.append({'stamp_s': float(np.interp(received, clock[:, 0], clock[:, 1])),
                                   'state': state})
    result = {'capture': str(path), 'intervals': results, 'status_changes': status_changes,
              'imu_samples': len(imu), 'matched_native_samples': len(pairs),
              'max_imu_gap_s': float(np.max(np.diff(imu[:, 0]))),
              'max_native_ros_difference': float(np.max(abs(differences[:, 0]-differences[:, 1])))}
    if (path / 'probe-imu.csv').exists():
        result['probe_impulses'] = analyze_probe(path, imu)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    with (out / 'timeseries.csv').open('w') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['stamp_s', 'truth_x', 'truth_y', 'truth_speed', 'estimate_x',
                         'estimate_y', 'estimate_speed', 'imu_ax', 'imu_gravity_x_diagnostic'])
        for row in estimate:
            stamp = row[0]
            reference = [float(np.interp(stamp, truth[:, 0], truth[:, k])) for k in (1, 2, 3)]
            sensor = [float(np.interp(stamp, imu[:, 0], imu[:, k])) for k in (4, 7)]
            writer.writerow([stamp, *reference, row[1], row[2], row[3], *sensor])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture, args.out), indent=2))


if __name__ == '__main__':
    main()
