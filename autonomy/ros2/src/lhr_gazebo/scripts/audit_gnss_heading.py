#!/usr/bin/env python3
"""Attribute GNSS heading jumps and compare held-out gyro corrections on identical data."""

import argparse
import csv
import json
import math
from pathlib import Path

from analyze_cone_path import planar, pose_at, pose_row
from analyze_stationary_cones import audit_motion, distribution
from lhr_state_estimation.ekf import VehicleEkf, wrap_angle
from lhr_state_estimation.gnss import LocalENU, PositionHistory
from lhr_vehicle import load_vehicle
import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message
import yaml


def timestamp(msg):
    return msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec


def read(capture):
    """Read only input/output topics needed for causal update attribution."""
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(capture / 'bag'), storage_id='mcap'),
                ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    data = {'imu': [], 'fixes': {}, 'decisions': [], 'odom': [], 'truth': []}
    selected = {'/lhr/imu/data', '/lhr/gnss/fix', '/lhr/state_estimation/gnss_status',
                '/lhr/vehicle/odom', '/lhr/vehicle/odom_truth'}
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic not in selected:
            continue
        msg = deserialize_message(raw, types[topic])
        if topic.endswith('/gnss_status'):
            data['decisions'].append(json.loads(msg.data))
        elif topic.endswith('/fix'):
            data['fixes'][timestamp(msg)] = msg
        elif topic.endswith('/data'):
            a, g = msg.linear_acceleration, msg.angular_velocity
            data['imu'].append([timestamp(msg), g.x, g.y, g.z, a.x, a.y, a.z])
        else:
            key = 'truth' if topic.endswith('odom_truth') else 'odom'
            data[key].append(pose_row(msg))
    data['imu'] = np.asarray(sorted(data['imu']))
    return data


def write_rows(path, rows):
    if not rows:
        return
    with path.open('w') as stream:
        keys = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def replay(data, parameters, start_s, gyro_bias=0., use_gnss=True, ax_offset=0.,
           heading_gain=1., initial_yaw_offset=0.):
    """Use production filter math and recorded correction times; never refit on score data."""
    initial = data['truth'][0][1:]
    ekf = VehicleEkf(parameters['sigma_accel'], parameters['sigma_yaw_accel'])
    ekf.reset(*initial[:2], planar(np.asarray(initial))[2]+initial_yaw_offset)
    history = PositionHistory(ekf, parameters['gnss_max_delay_s'])
    origin = LocalENU(
        parameters['gnss_origin_latitude_deg'], parameters['gnss_origin_longitude_deg'],
        parameters['gnss_origin_altitude_m'])
    lever = load_vehicle().gnss.position_m
    events = sorted(data['decisions'], key=lambda d: d['state_stamp_s']) if use_gnss else []
    events = [d for d in events if d['stamp_s'] >= start_s]
    event_index, previous, previous_accel = 0, None, 0.
    states, corrections = [], []
    for row in data['imu']:
        stamp = int(row[0])
        if stamp < round(start_s*1e9):
            continue
        accel, gyro = float(row[4])-ax_offset, float(row[3])-gyro_bias
        if previous is not None:
            ekf.predict((stamp-previous)*1e-9, .5*(accel+previous_accel))
        ekf.update_yaw_rate(gyro, parameters['yaw_rate_variance'])
        previous, previous_accel = stamp, accel
        history.record(stamp, accel, gyro, parameters['yaw_rate_variance'])
        while (event_index < len(events)
               and round(events[event_index]['state_stamp_s']*1e9) <= stamp):
            event = events[event_index]
            event_index += 1
            fix_stamp = round(event['stamp_s']*1e9)
            if fix_stamp not in data['fixes']:
                continue
            fix = data['fixes'][fix_stamp]
            point = origin.to_enu(fix.latitude, fix.longitude, fix.altitude)[:2]
            covariance = np.asarray(fix.position_covariance).reshape(3, 3)[:2, :2]
            accepted, reason, nis = history.correct(
                fix_stamp, point, covariance, lever, parameters['gnss_gate_chi2'], heading_gain)
            corrections.append({'stamp_s': fix_stamp*1e-9, 'state_stamp_s': stamp*1e-9,
                                'accepted': accepted, 'reason': reason, 'nis': nis,
                                **history.last_correction})
        states.append([stamp*1e-9, *ekf.x])
    return np.asarray(states), corrections


def jump_metrics(decisions, start=8., end=20.):
    """Measure actual update discontinuities, separate from continuous IMU propagation."""
    rows = [d for d in decisions if start <= d['state_stamp_s'] <= end and d['accepted']
            and 'current_yaw_delta_rad' in d]
    if not rows:
        return None
    deltas = [math.degrees(d['current_yaw_delta_rad']) for d in rows]
    direct = [math.degrees(d['fix_yaw_delta_rad']) for d in rows]
    return {
        'accepted_instrumented_fixes': len(rows),
        'absolute_current_yaw_jump_deg': distribution(np.abs(deltas)),
        'sum_absolute_current_yaw_jumps_deg': float(np.abs(deltas).sum()),
        'sum_signed_current_yaw_jumps_deg': float(np.sum(deltas)),
        'absolute_direct_fix_yaw_jump_deg': distribution(np.abs(direct)),
        'fixes_changing_current_yaw_over_0_1deg': int((np.abs(deltas) > .1).sum()),
        'absolute_speed_before_fix_m_s': distribution(
            [abs(d['current_speed_before_m_s']) for d in rows]),
        'nonzero_yaw_position_cross_covariance': int(sum(
            np.linalg.norm(d['fix_yaw_position_cross_covariance']) > 1e-12 for d in rows)),
    }


def score(states, truth, corrections, start=8., end=20.):
    """Score held-out state errors only at sensor times covered by reference truth."""
    rows = []
    for row in states[::20]:
        if not start <= row[0] <= end:
            continue
        actual = pose_at(truth, row[0])
        if actual is None:
            continue
        rows.append({'stamp_s': row[0], 'position_error_m': float(
            np.linalg.norm(row[1:3]-actual[:2])),
            'signed_heading_error_deg': math.degrees(wrap_angle(row[3]-planar(actual)[2])),
            'estimated_speed_m_s': row[4]})
    return rows, {
        'position_error_m': distribution([r['position_error_m'] for r in rows]),
        'absolute_heading_error_deg': distribution(
            [abs(r['signed_heading_error_deg']) for r in rows]),
        'absolute_estimated_speed_m_s': distribution(
            [abs(r['estimated_speed_m_s']) for r in rows]),
        'gnss_jumps': jump_metrics(corrections, start, end),
    }


def analyze(capture, out):
    out.mkdir(parents=True, exist_ok=False)
    data = read(capture)
    native = np.loadtxt(capture / 'native-imu.csv', delimiter=',', skiprows=1)
    native[:, 0] = np.rint(native[:, 0]*1e9)
    by_stamp = {int(row[0]): row[1:] for row in native}
    differences = [np.max(abs(row[1:]-by_stamp[int(row[0])])) for row in data['imu']
                   if int(row[0]) in by_stamp]
    if (not differences or len(differences) != len(data['imu'])
            or max(differences) > 1e-12):
        raise ValueError('Native and bridged IMU samples do not agree at shared timestamps')
    stream_audit = {
        'native_samples': len(native), 'recorded_ros_samples': len(data['imu']),
        'shared_samples': len(differences),
        'shared_sample_max_component_difference': float(max(differences)),
        'note': 'Use complete native sensor inputs after verifying identical bridged values; '
                'bag recording can miss messages that the live EKF received.',
    }
    data['imu'] = native[native[:, 0] <= 20e9]
    parameters = yaml.safe_load((capture / 'ekf_node-params.yaml').read_text())
    parameters = parameters['/ekf_node']['ros__parameters']
    truth, calibration_motion = audit_motion(capture, 2., 5.)
    _, evaluation_motion = audit_motion(capture, 8., 20.)
    if not (calibration_motion['stationary_verified']
            and evaluation_motion['stationary_verified']):
        raise ValueError('Calibration/evaluation window was not independently stationary')
    imu = data['imu']
    calibration = imu[(imu[:, 0] >= 2e9) & (imu[:, 0] < 5e9)]
    held_out = imu[(imu[:, 0] >= 8e9) & (imu[:, 0] <= 20e9)]
    if len(calibration) < 1000 or len(held_out) < 1000:
        raise ValueError('Insufficient disjoint calibration/evaluation samples')
    bias = float(calibration[:, 3].mean())
    ax_offset = float(calibration[:, 4].mean())
    summary = {
        'capture': str(capture), 'calibration_window_s': [2, 5], 'evaluation_window_s': [8, 20],
        'calibration_motion': calibration_motion, 'evaluation_motion': evaluation_motion,
        'input_stream_audit': stream_audit,
        'gyro_offset_rad_s': bias, 'gyro_offset_deg_s': math.degrees(bias),
        'initial_yaw_rad': planar(np.asarray(data['truth'][0][1:]))[2],
        'calibration_samples': len(calibration), 'held_out_samples': len(held_out),
        'held_out_mean_raw_gyro_deg_s': float(np.degrees(held_out[:, 3].mean())),
        'held_out_mean_corrected_gyro_deg_s': float(np.degrees((held_out[:, 3]-bias).mean())),
        'stationary_forward_offset_m_s2': ax_offset,
        'actual_live_jumps': jump_metrics(data['decisions']),
        'conditions': {},
        'limits': 'Offline counterfactual: same recorded native IMUs/fixes, common restart at 5 s '
                  'using the original one-time surveyed-start-pose proxy; physics verifies '
                  'stationarity and scores errors, never computes the offsets. Live GNSS '
                  'consumption times are retained. This is not live startup calibration. '
                  'Forward-offset control combines bias and gravity projection under a '
                  'fixed stationary attitude; it is not a general accelerometer calibration.',
    }
    write_rows(out / 'live-corrections.csv', data['decisions'])
    for tag, gyro, gnss, accel in [
            ('raw_no_gnss', 0., False, 0.), ('raw_gnss', 0., True, 0.),
            ('gyro_corrected_no_gnss', bias, False, 0.),
            ('gyro_corrected_gnss', bias, True, 0.),
            ('gyro_and_stationary_forward_offset_gnss', bias, True, ax_offset)]:
        states, corrections = replay(data, parameters, 5., gyro, gnss, accel)
        rows, metrics = score(states, truth, corrections)
        summary['conditions'][tag] = metrics
        write_rows(out / f'{tag}-states.csv', rows)
        write_rows(out / f'{tag}-corrections.csv', corrections)
    # Reproduce the original live start as a separate audit, not an accuracy result.
    original_start = data['truth'][0][0]
    states, corrections = replay(data, parameters, original_start)
    by_stamp = {round(r[0]*1e9): r for r in states}
    correction_stamps = {round(d['state_stamp_s']*1e9) for d in corrections}
    differences = []
    for row in data['odom']:
        key = round(row[0]*1e9)
        if 8 <= row[0] <= 20 and key in by_stamp and key not in correction_stamps:
            predicted = by_stamp[key]
            delta = wrap_angle(predicted[3]-planar(np.asarray(row[1:]))[2])
            differences.append(abs(math.degrees(delta)))
    summary['original_start_replay_audit'] = {
        'heading_difference_deg': distribution(differences),
        'note': 'Recorded bag inputs can omit samples received by the live EKF; compare '
                'noncoincident output/correction timestamps to avoid callback-order ambiguity.',
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


def plot(out, summary):
    """Keep actual measured update jumps distinct from offline calibration cases."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    with (out / 'live-corrections.csv').open() as stream:
        live = [row for row in csv.DictReader(stream)
                if row['accepted'] == 'True' and row.get('current_yaw_delta_rad')
                and 8 <= float(row['state_stamp_s']) <= 20]
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.4), constrained_layout=True)
    if live:
        time = [float(r['state_stamp_s']) for r in live]
        for key, label in [('current_yaw_before_rad', 'Before fix/replay'),
                           ('current_yaw_after_rad', 'After fix/replay')]:
            yaw = [math.degrees(wrap_angle(float(r[key])-summary['initial_yaw_rad']))
                   for r in live]
            axes[0, 0].plot(time, yaw, label=label, linewidth=.9)
        axes[0, 1].scatter(
            time, [math.degrees(float(r['current_yaw_delta_rad'])) for r in live],
            s=6, label='Actual current-state GPS/replay jump')
        axes[0, 1].axhline(
            summary['gyro_offset_deg_s']*.02, color='gray', linestyle='--',
            label='Mean raw gyro drift over 20 ms')
    for index, tag in enumerate(summary['conditions']):
        rows = np.genfromtxt(out / f'{tag}-states.csv', delimiter=',', names=True)
        axes[1, 0].plot(
            rows['stamp_s'], rows['signed_heading_error_deg'],
            color=f'C{index}', label=tag.replace('_', ' '))
        axes[1, 1].plot(rows['stamp_s'], rows['estimated_speed_m_s'], color=f'C{index}')
    axes[0, 0].set_ylabel('Actual live signed heading error (deg)')
    axes[0, 1].set_ylabel('Heading change per GPS update (deg)')
    axes[1, 0].set_ylabel('Offline signed heading error (deg)')
    axes[1, 1].set_ylabel('Offline estimated speed (m/s), truth = 0')
    for ax in axes.flat:
        ax.set_xlabel('Simulation time (s)')
        ax.grid(alpha=.25)
    axes[0, 0].legend(fontsize=7)
    axes[0, 1].legend(fontsize=7)
    axes[1, 0].legend(fontsize=6)
    fig.suptitle('Stationary seed 3: actual GPS jumps and identical-input offline controls\n'
                 'Held-out 8–20 s; offsets measured at 2–5 s; offline cases restart at 5 s')
    fig.savefig(out / 'heading-attribution.png', dpi=170)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--plot', action='store_true',
                        help='render the saved audit with Matplotlib')
    args = parser.parse_args()
    summary = analyze(args.capture, args.out)
    if args.plot:
        plot(args.out, summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
