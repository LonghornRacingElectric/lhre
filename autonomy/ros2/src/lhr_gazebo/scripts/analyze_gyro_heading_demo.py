#!/usr/bin/env python3
"""Verify live startup calibration, GPS angular isolation and physical run results."""

import argparse
import csv
import json
import math
from pathlib import Path

from analyze_cone_path import planar, pose_at
from analyze_stationary_cones import audit_motion, distribution
from audit_gnss_heading import jump_metrics, read, write_rows
import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message
import yaml


def analyze(capture, out):
    out.mkdir(parents=True, exist_ok=False)
    data = read(capture)
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(capture / 'bag'), storage_id='mcap'),
                ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    calibration, first_drive, first_emergency, clock = [], None, None, 0.
    safety_trips = []
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic not in ('/lhr/state_estimation/gyro_calibration', '/clock',
                         '/lhr/mission/status', '/lhr/safety/status'):
            continue
        msg = deserialize_message(raw, types[topic])
        if topic == '/clock':
            clock = msg.clock.sec+msg.clock.nanosec*1e-9
        elif topic.endswith('gyro_calibration'):
            calibration.append(json.loads(msg.data))
        elif topic == '/lhr/safety/status':
            if msg.message != 'NONE' and (not safety_trips or safety_trips[-1][1] != msg.message):
                safety_trips.append([clock, msg.message])
        elif msg.data == 'DRIVING' and first_drive is None:
            first_drive = clock
        elif msg.data == 'EMERGENCY' and first_emergency is None:
            first_emergency = clock
    ready = next((s for s in calibration if s['state'] == 'ready'), None)
    if ready is None:
        last_status = calibration[-1] if calibration else None
        raise ValueError(f'Calibration never completed: {last_status}')
    native = np.loadtxt(capture / 'native-imu.csv', delimiter=',', skiprows=1)
    probe = np.loadtxt(capture / 'probe-odom3d.csv', delimiter=',', skiprows=1)
    truth = probe[:, :8]
    start, end = ready['window_start_s'], ready['window_end_s']
    selected = native[(native[:, 0] >= start-1e-9) & (native[:, 0] <= end+1e-9)]
    _, motion = audit_motion(capture, start, end)
    motion.pop('stationary_verified')
    motion['stationary_verified_over_calibration_window'] = bool(
        motion['physics_max_translation_from_window_start_m'] < .02
        and motion['physics_max_heading_change_deg'] < .2
        and motion['physics_max_linear_speed_m_s'] < .001
        and motion['max_abs_commanded_speed_m_s'] == 0
        and (first_drive is None or first_drive > end))
    rows = []
    for row in data['odom']:
        actual = pose_at(truth, row[0])
        if actual is None:
            continue
        yaw = planar(np.asarray(row[1:]))[2]-planar(actual)[2]
        rows.append({'stamp_s': row[0],
                     'position_error_m': float(np.linalg.norm(np.asarray(row[1:3])-actual[:2])),
                     'signed_heading_error_deg': math.degrees(
                         (yaw+math.pi) % (2*math.pi)-math.pi)})
    score = [r for r in rows if 8 <= r['stamp_s'] <= 20]
    gyro = native[(native[:, 0] >= 8) & (native[:, 0] <= 20), 3]
    params = yaml.safe_load((capture / 'ekf_node-params.yaml').read_text())
    contacts = json.loads((capture / 'cone-contacts.json').read_text())
    actual = params['/ekf_node']['ros__parameters']
    summary = {
        'capture': str(capture), 'actual_calibrate_gyro': actual['calibrate_gyro'],
        'actual_gnss_heading_gain': actual['gnss_heading_gain'], 'calibration': ready,
        'actual_use_stationary_updates': actual.get('use_stationary_updates', False),
        'calibration_physics_motion': motion,
        'native_mean_gyro_z_over_calibration_rad_s': float(selected[:, 3].mean()),
        'bias_vs_native_mean_difference_rad_s': float(ready['bias_z_rad_s']-selected[:, 3].mean()),
        'first_odom_stamp_s': data['odom'][0][0],
        'no_odom_before_calibration': data['odom'][0][0] >= end-1e-9,
        'first_driving_clock_s': first_drive,
        'first_emergency_clock_s': first_emergency, 'observed_safety_faults': safety_trips,
        'score_window_s': [8, 20],
        'held_out_corrected_gyro_mean_deg_s': float(np.degrees(
            (gyro-ready['bias_z_rad_s']).mean())),
        'heading_error_deg': distribution([abs(r['signed_heading_error_deg']) for r in score]),
        'position_error_m': distribution([r['position_error_m'] for r in score]),
        'full_recording_heading_error_deg': distribution(
            [abs(r['signed_heading_error_deg']) for r in rows]),
        'actual_gps_heading_jumps': jump_metrics(data['decisions']),
        'first_native_cone_contact': contacts[:1],
        'native_capture_end_s': float(native[-1, 0]),
        'limits': 'Live simulator profile, not physical-car calibration or validation of '
                  'uncertain initial heading. Existing one-time Gazebo start-pose seed remains. '
                  'Stationary corrections, if enabled, are experimental; no accelerometer '
                  'calibration or map retention change.',
    }
    metrics = []
    if (capture / 'metrics.csv').exists():
        with (capture / 'metrics.csv').open() as stream:
            metrics = list(csv.DictReader(stream))
    summary['metrics'] = metrics[-1] if metrics else None
    (out / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    write_rows(out / 'errors.csv', rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture, args.out), indent=2))


if __name__ == '__main__':
    main()
