#!/usr/bin/env python3
"""Check native/ROS identity and the acceleration-course input signal."""

import argparse
import csv
import json
from pathlib import Path

from evaluate_imu_only import read_capture
from lhr_vehicle import Imu, load_vehicle
import numpy as np


def native_comparison(path, imu):
    """Compare the intersection of stamped native and bridged samples."""
    native = np.loadtxt(path / 'native-imu.csv', delimiter=',', skiprows=1)
    ros_by_stamp = {round(row[0], 9): row[1:] for row in imu}
    native_by_stamp = {round(row[0], 9): row[1:] for row in native}
    matches = [(native_by_stamp[stamp], value) for stamp, value in ros_by_stamp.items()
               if stamp in native_by_stamp]
    if not matches:
        raise ValueError('No matched native/ROS IMU timestamps')
    delta = np.asarray(matches)
    max_difference = float(np.max(abs(delta[:, 0]-delta[:, 1])))
    return {
        'native_samples': len(native), 'ros_samples': len(imu),
        'matched_samples': len(matches), 'ros_match_fraction': len(matches)/len(imu),
        'max_native_ros_difference': max_difference,
    }


def check(path):
    """Compare matched timestamps and a fixed straight acceleration interval."""
    options = json.loads((path / 'capture-options.json').read_text())
    if options['style'] != 'acceleration' or options['noise'] != 'false':
        raise ValueError('This diagnostic requires a noise-free acceleration capture')
    imu, truth, _ = read_capture(path)
    comparison = native_comparison(path, imu)
    begin, end = 12.0, 15.0
    if imu[0, 0] > 2 or imu[-1, 0] < end or truth[-1, 0] < end:
        raise ValueError('Capture does not cover the required measurement intervals')
    held = imu[(imu[:, 0] >= 2) & (imu[:, 0] <= 7)]
    held_truth = truth[(truth[:, 0] >= 2) & (truth[:, 0] <= 7)]
    if len(held) < 400 or np.max(abs(held_truth[:, 4])) > 0.001:
        raise ValueError('Missing declared stationary calibration interval')
    offset = float(np.mean(held[:, 4]))
    moving = imu[(imu[:, 0] >= begin) & (imu[:, 0] <= end)]
    # Include exact interval endpoints in the trapezoidal integral.
    interior = moving[(moving[:, 0] > begin) & (moving[:, 0] < end), 0]
    times = np.r_[begin, interior, end]
    accel = np.interp(times, imu[:, 0], imu[:, 4]) - offset
    observed_dv = float(np.diff(np.interp([begin, end], truth[:, 0], truth[:, 4]))[0])
    if observed_dv < 2:
        raise ValueError('Reference vehicle did not accelerate sufficiently')
    integrated_dv = float(np.trapz(accel, times))
    expected = observed_dv/(end-begin)
    measured = integrated_dv/(end-begin)
    relative_error = abs(measured-expected)/abs(expected)
    # These distinguish the known missing-signal control from the force-mode
    # positive control; they are not an estimator or vehicle-performance gate.
    recovered = relative_error < 0.1
    force_drive = options.get('drive_mode') == 'force' or options['force_drive']
    expects_acceleration = force_drive or options['imu_rate'] == 1000
    signal_matches_control = recovered if expects_acceleration else abs(measured/expected) < 0.05
    return {
        'capture': path.name,
        'force_drive': force_drive,
        'imu_rate_hz': (options['imu_rate']
                        or options.get('imu_config', {}).get('update_rate_hz', 100)),
        **comparison,
        'stationary_ax_offset_m_s2': offset,
        'stationary_ax_stddev_m_s2': float(np.std(held[:, 4], ddof=1)),
        'speed_at_12s_m_s': float(np.interp(begin, truth[:, 0], truth[:, 4])),
        'speed_at_15s_m_s': float(np.interp(end, truth[:, 0], truth[:, 4])),
        'expected_average_accel_m_s2': expected,
        'imu_average_accel_m_s2': measured,
        'imu_accel_stddev_m_s2': float(np.std(moving[:, 4], ddof=1)),
        'truth_delta_v_m_s': observed_dv, 'imu_integrated_delta_v_m_s': integrated_dv,
        'relative_accel_error': relative_error, 'acceleration_recovered': recovered,
        'diagnostic_pass': comparison['ros_match_fraction'] >= 0.99
        and comparison['max_native_ros_difference'] == 0 and signal_matches_control,
    }


def motion_check(path):
    """Check installed force actuation, retained noise, braking and gyro turns."""
    options = json.loads((path / 'capture-options.json').read_text())
    if options.get('drive_mode') != 'force':
        raise ValueError('Motion validation requires the force-driven model')
    imu, truth, phases = read_capture(path)
    held = imu[(imu[:, 0] >= 2) & (imu[:, 0] <= 7)]
    held_truth = truth[(truth[:, 0] >= 2) & (truth[:, 0] <= 7)]
    if len(held) < 400 or np.max(abs(held_truth[:, 4])) > 0.001:
        raise ValueError('Missing stationary reference interval')
    offsets = held[:, 1:].mean(axis=0)
    sigma = held[:, 1:].std(axis=0, ddof=1)
    config = Imu(**options['imu_config']) if 'imu_config' in options else load_vehicle().imu
    expected_sigma = np.array([config.gyro_stddev_rad_s]*3 + [config.accel_stddev_m_s2]*3)
    noise_ok = (bool(np.all(abs(sigma/expected_sigma-1) < 0.25))
                if options['noise'] == 'true' else bool(np.all(sigma < 0.001)))
    result = {
        'capture': path.name, 'style': options['style'], 'noise': options['noise'],
        **native_comparison(path, imu),
        'imu_median_dt_s': float(np.median(np.diff(imu[:, 0]))),
        'imu_max_gap_s': float(np.max(np.diff(imu[:, 0]))),
        'stationary_sigma': sigma.tolist(), 'stationary_offsets': offsets.tolist(),
        'noise_spread_pass': noise_ok,
    }
    signal_ok = True
    if options['style'] == 'acceleration':
        for label, begin, end in [('accelerating', 12.0, 15.0), ('braking', 28.0, 32.0)]:
            if min(imu[-1, 0], truth[-1, 0]) < end:
                raise ValueError('Capture does not cover acceleration and braking intervals')
            times = np.r_[begin, imu[(imu[:, 0] > begin) & (imu[:, 0] < end), 0], end]
            expected = float(np.diff(np.interp([begin, end], truth[:, 0], truth[:, 4]))[0])
            measured = float(np.trapz(np.interp(times, imu[:, 0], imu[:, 4])-offsets[3], times))
            error = abs(measured-expected)/max(abs(expected), 1e-12)
            result[label] = {'truth_delta_v_m_s': expected,
                             'imu_integrated_delta_v_m_s': measured, 'relative_error': error}
            direction_ok = expected > 2 if label == 'accelerating' else expected < -2
            signal_ok &= error < 0.1 and direction_ok
        result['final_speed_m_s'] = float(truth[-1, 4])
        signal_ok &= abs(truth[-1, 4]) < 0.01 and any(s == 'FINISHED' for _, s in phases)
    elif options['style'] == 'oval':
        begin, end = 12.0, 44.0
        if min(imu[-1, 0], truth[-1, 0]) < end:
            raise ValueError('Capture does not cover the turning interval')
        times = np.r_[begin, imu[(imu[:, 0] > begin) & (imu[:, 0] < end), 0], end]
        yaw = np.unwrap(truth[:, 3])
        expected = float(np.diff(np.interp([begin, end], truth[:, 0], yaw))[0])
        measured = float(np.trapz(np.interp(times, imu[:, 0], imu[:, 3])-offsets[2], times))
        result['turn'] = {'truth_yaw_change_rad': expected,
                          'imu_integrated_yaw_change_rad': measured,
                          'absolute_error_rad': abs(measured-expected)}
        signal_ok &= abs(expected) > 2 and abs(measured-expected) < 0.02
    else:
        raise ValueError('Motion validation supports acceleration or oval captures')
    result['validation_pass'] = bool(
        noise_ok and signal_ok and result['ros_match_fraction'] >= 0.99
        and result['max_native_ros_difference'] == 0 and result['imu_max_gap_s'] <= 0.031)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('captures', type=Path, nargs='+')
    parser.add_argument('--motion', action='store_true',
                        help='validate force control with noise, braking and turns; write JSON')
    args = parser.parse_args()
    rows = [(motion_check if args.motion else check)(path) for path in args.captures]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.motion:
        args.out.write_text(json.dumps(rows, indent=2) + '\n')
    else:
        with args.out.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(rows, indent=2))
    if not all(row['validation_pass' if args.motion else 'diagnostic_pass'] for row in rows):
        raise SystemExit('Acceleration-path diagnostic did not match its controls')


if __name__ == '__main__':
    main()
