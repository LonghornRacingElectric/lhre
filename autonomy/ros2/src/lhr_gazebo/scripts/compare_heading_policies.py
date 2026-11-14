#!/usr/bin/env python3
"""Compare gyro confidence and angular GNSS gain on recorded and synthetic controls."""

import argparse
import json
import math
from pathlib import Path

from analyze_stationary_cones import audit_motion
from audit_gnss_heading import read, replay, score, write_rows
from lhr_state_estimation.gnss import LocalENU
from lhr_vehicle import load_vehicle
import numpy as np
from sensor_msgs.msg import NavSatFix
import yaml


def load_recorded(capture, end):
    """Use matched native IMUs, and independently verify the declared calibration window."""
    data = read(capture)
    native = np.loadtxt(capture / 'native-imu.csv', delimiter=',', skiprows=1)
    native[:, 0] = np.rint(native[:, 0]*1e9)
    lookup = {int(r[0]): r[1:] for r in native}
    if any(int(r[0]) not in lookup or not np.array_equal(r[1:], lookup[int(r[0])])
           for r in data['imu']):
        raise ValueError('Native/ROS IMU mismatch')
    data['imu'] = native[native[:, 0] <= end*1e9]
    truth = np.loadtxt(capture / 'probe-odom3d.csv', delimiter=',', skiprows=1)
    _, motion = audit_motion(capture, 2., 5.)
    if (motion['physics_max_translation_from_window_start_m'] > .001
            or motion['physics_max_heading_change_deg'] > .1
            or motion['physics_max_linear_speed_m_s'] > .001
            or motion['max_abs_commanded_speed_m_s'] != 0):
        raise ValueError('Declared calibration window not physically stationary')
    params = yaml.safe_load((capture / 'ekf_node-params.yaml').read_text())
    return data, params['/ekf_node']['ros__parameters'], truth[:, :8], motion


def synthetic(kind, seed, end=40.):
    """Generate an ideal planar control with known motion and independent noisy sensors."""
    vehicle = load_vehicle()
    rng = np.random.default_rng(seed)
    dt, rate = .01, 100.
    gyro_sigma = vehicle.imu.gyro_noise_density_rad_s_sqrt_hz*math.sqrt(rate/2)
    accel_sigma = vehicle.imu.accel_noise_density_m_s2_sqrt_hz*math.sqrt(rate/2)
    gyro_bias = rng.normal(0., vehicle.imu.gyro_bias_stddev_rad_s)
    accel_bias = rng.normal(0., vehicle.imu.accel_bias_stddev_m_s2)
    origin = LocalENU(0, 0, 0)
    data = {'imu': [], 'fixes': {}, 'decisions': [], 'truth': []}
    truth = []
    x, y, yaw, v, old_rate, old_accel = 0., 0., 0., 0., 0., 0.
    for k in range(round(end/dt)+1):
        t, ns = k*dt, round(k*dt*1e9)
        accel = (1. if kind == 'straight' and 7 <= t < 10 else
                 .25 if kind == 'slow_turn' and 7 <= t < 11 else 0.)
        turn = .08 if kind == 'slow_turn' and t >= 11 else 0.
        if k:
            x += v*math.cos(yaw)*dt
            y += v*math.sin(yaw)*dt
            yaw += old_rate*dt
            v += old_accel*dt
        q = [0., 0., math.sin(yaw/2), math.cos(yaw/2)]
        truth.append([t, x, y, 0., *q])
        data['imu'].append([ns, 0., 0., turn+gyro_bias+rng.normal(0., gyro_sigma),
                            accel+accel_bias+rng.normal(0., accel_sigma), 0., 9.80665])
        if k % 2 == 0:
            mount = vehicle.gnss.position_m
            c, s = math.cos(yaw), math.sin(yaw)
            point = np.array([x+c*mount[0]-s*mount[1], y+s*mount[0]+c*mount[1], mount[2]])
            sigmas = [vehicle.gnss.horizontal_stddev_m]*2+[vehicle.gnss.vertical_stddev_m]
            point += rng.normal(0., sigmas)
            fix = NavSatFix()
            fix.latitude, fix.longitude, fix.altitude = origin.to_geodetic(*point)
            fix.position_covariance = np.diag(np.square(sigmas)).ravel().tolist()
            data['fixes'][ns] = fix
            data['decisions'].append({'stamp_s': t, 'state_stamp_s': t+vehicle.gnss.latency_s})
        old_rate, old_accel = turn, accel
    data['imu'] = np.asarray(data['imu'])
    data['truth'] = [truth[0]]
    params = {'sigma_accel': 1., 'sigma_yaw_accel': 1., 'gnss_max_delay_s': 1.,
              'gnss_origin_latitude_deg': 0., 'gnss_origin_longitude_deg': 0.,
              'gnss_origin_altitude_m': 0., 'yaw_rate_variance': .0004, 'gnss_gate_chi2': 25.}
    description = {'kind': kind, 'seed': seed, 'imu_rate_hz': rate,
                   'gyro_bias_rad_s': float(gyro_bias), 'accel_bias_m_s2': float(accel_bias),
                   'gyro_white_std_rad_s': gyro_sigma, 'accel_white_std_m_s2': accel_sigma,
                   'limits': 'Ideal planar synthetic motion, not Gazebo or physical validation; '
                             'stationary until 7 s, then straight acceleration or a slow turn.'}
    return data, params, np.asarray(truth), description


def recovery(rows, motion_start=7., threshold_deg=2.):
    """Find first two continuous seconds within heading tolerance after motion starts."""
    for i, row in enumerate(rows):
        if row['stamp_s'] < motion_start:
            continue
        window = [r for r in rows[i:] if r['stamp_s'] <= row['stamp_s']+2.]
        if window[-1]['stamp_s']-row['stamp_s'] < 1.9:
            continue
        if max(abs(r['signed_heading_error_deg']) for r in window) <= threshold_deg:
            return row['stamp_s']-motion_start
    return None


def compare(data, parameters, truth, name, out, end, offsets):
    """Hold calibration, initial covariance, GPS inputs and noise fixed across policies."""
    imu = data['imu']
    cal = imu[(imu[:, 0] >= 2e9) & (imu[:, 0] < 5e9)]
    bias, measured_variance = float(cal[:, 3].mean()), float(cal[:, 3].var(ddof=1))
    measured_variance *= 1+1/len(cal)
    policies = [('default_gyro_gain1', parameters['yaw_rate_variance'], 1.),
                ('measured_gyro_gain1', measured_variance, 1.),
                ('measured_gyro_gain025', measured_variance, .25),
                ('measured_gyro_gain01', measured_variance, .1),
                ('measured_gyro_gain0', measured_variance, 0.)]
    results = []
    for offset in offsets:
        for policy, variance, gain in policies:
            params = {**parameters, 'yaw_rate_variance': variance}
            states, corrections = replay(
                data, params, 5., bias, heading_gain=gain,
                initial_yaw_offset=math.radians(offset))
            rows, metrics = score(states, truth, corrections, 8., end)
            tail = [r for r in rows if r['stamp_s'] >= end-2]
            jump = metrics['gnss_jumps']
            record = {
                'dataset': name, 'initial_yaw_offset_deg': offset, 'policy': policy,
                'gyro_variance_rad2_s2': variance, 'heading_gain': gain,
                'gyro_calibration_rad_s': bias,
                'heading_p95_deg': metrics['absolute_heading_error_deg']['p95'],
                'heading_max_deg': metrics['absolute_heading_error_deg']['max'],
                'final_2s_mean_abs_heading_deg': float(np.mean(
                    [abs(r['signed_heading_error_deg']) for r in tail])),
                'position_p95_m': metrics['position_error_m']['p95'],
                'position_max_m': metrics['position_error_m']['max'],
                'false_or_estimated_speed_p95_m_s': metrics['absolute_estimated_speed_m_s']['p95'],
                'gnss_heading_jump_p95_deg': jump['absolute_current_yaw_jump_deg']['p95'],
                'recovery_within_2deg_for_2s_after_7s': (
                    None if name.startswith('stationary') else recovery(rows)),
                'accepted_fixes_in_score_window': jump['accepted_instrumented_fixes'],
            }
            results.append(record)
            write_rows(out / f'{name}-yaw{offset}-{policy}.csv', rows)
    return results


def plot(out, rows):
    """Show stationary precision and initial-heading recovery as different criteria."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    panels = [('stationary_recorded', 0, 'Recorded stationary: correct initial heading')]
    if (out / 'moving_recorded-yaw10-default_gyro_gain1.csv').exists():
        panels.append(('moving_recorded', 10, 'Recorded moving: initial heading +10°'))
    panels.append(('synthetic_straight_seed3', 10, 'Synthetic straight: initial heading +10°'))
    fig, axes = plt.subplots(1, len(panels), figsize=(5.5*len(panels), 4.5),
                             constrained_layout=True)
    for index, policy in enumerate(dict.fromkeys(r['policy'] for r in rows)):
        for ax, (name, offset, title) in zip(axes, panels):
            path = out / f'{name}-yaw{offset}-{policy}.csv'
            a = np.genfromtxt(path, delimiter=',', names=True)
            ax.plot(a['stamp_s'], a['signed_heading_error_deg'],
                    color=f'C{index}', label=policy.replace('_', ' '))
            ax.set_title(title, fontsize=10)
    for ax in axes:
        ax.set(xlabel='Time (s)', ylabel='Signed heading error (deg)')
        ax.grid(alpha=.25)
    axes[0].legend(fontsize=7)
    fig.suptitle('Calibrated gyro: confidence tuning versus selective GPS angular gain')
    fig.savefig(out / 'heading-policy-comparison.png', dpi=170)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stationary_capture', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--moving-capture', type=Path)
    parser.add_argument('--plot', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    rows, descriptions = [], {}
    data, params, truth, meta = load_recorded(args.stationary_capture, 20.)
    descriptions['stationary_recorded'] = meta
    rows.extend(compare(data, params, truth, 'stationary_recorded', args.out, 20., [0, -10, 10]))
    if args.moving_capture:
        data, params, truth, meta = load_recorded(args.moving_capture, 12.5)
        descriptions['moving_recorded'] = meta
        rows.extend(compare(data, params, truth, 'moving_recorded', args.out, 12.5, [0, -10, 10]))
    for kind in ['straight', 'slow_turn']:
        for seed in [3, 7, 11]:
            name = f'synthetic_{kind}_seed{seed}'
            data, params, truth, meta = synthetic(kind, seed)
            descriptions[name] = meta
            rows.extend(compare(data, params, truth, name, args.out, 40., [0, -10, 10]))
    write_rows(args.out / 'comparison.csv', rows)
    (args.out / 'sources.json').write_text(json.dumps(descriptions, indent=2)+'\n')
    if args.plot:
        plot(args.out, rows)
    print(f'Saved {len(rows)} policy/initial-heading comparisons to {args.out}')


if __name__ == '__main__':
    main()
