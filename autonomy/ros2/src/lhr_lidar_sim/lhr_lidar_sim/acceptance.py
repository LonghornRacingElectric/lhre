"""Compare simulated returns with published stationary Mid-360 acceptance runs."""

import argparse
import csv
from dataclasses import asdict
import hashlib
from importlib.resources import files
import json
import math
from pathlib import Path

from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose
import numpy as np


REFERENCE_COMMIT = '52fb09e6a71e788323171ac86d29c1fae98e08c3'


def availability(counts, window_frames=5, minimum_points=3):
    """Measure return availability, including empty frames and warmup."""
    counts = np.asarray(counts)
    totals = (np.convolve(counts, np.ones(window_frames, dtype=int), mode='full')[:len(counts)]
              if len(counts) else np.array([]))
    return {'points_per_frame': float(counts.mean()) if len(counts) else 0.0,
            'frames_hit_pct': float(100 * (counts > 0).mean()) if len(counts) else 0.0,
            'stack_available_pct': float(100 * (totals >= minimum_points).mean())
            if len(counts) else 0.0}


def gravity_rotation(imu):
    """Return the sensor-to-level rotation measured from a stationary IMU."""
    up = np.asarray(imu[:, 3:6].mean(axis=0), dtype=float)
    norm = np.linalg.norm(up)
    if not np.isfinite(norm) or norm == 0:
        raise ValueError('Capture has no valid gravity direction')
    up /= norm
    # Rodrigues rotation from measured up to world z. Antipodal gravity is
    # rejected because an upside-down capture requires explicit mount metadata.
    axis = np.cross(up, [0., 0., 1.])
    cosine = float(up[2])
    if cosine < -.999:
        raise ValueError('Upside-down capture requires an explicit mount transform')
    skew = np.array([[0., -axis[2], axis[1]], [axis[2], 0., -axis[0]],
                     [-axis[1], axis[0], 0.]])
    rotation = np.eye(3) + skew + skew @ skew / (1. + cosine)
    return rotation


def capture_counts(path, row, frame_sec=.1):
    """Count reference-cone ROI returns in a stationary acceptance NPZ capture."""
    with np.load(path, allow_pickle=False) as data:
        xyz, times, imu = data['xyz'], data['t'], data['imu']
    if xyz.ndim != 2 or xyz.shape[1] != 3 or times.shape != (len(xyz),):
        raise ValueError('Capture must contain xyz[N,3] and t[N] in seconds')
    if imu.ndim != 2 or imu.shape[1] < 6 or not len(imu):
        raise ValueError('Capture must contain imu[N,6] with acceleration in columns 3:6')
    rotation = gravity_rotation(imu)
    points = np.asarray(xyz) @ rotation.T
    height = points[:, 2] + float(row['sensor_height_m'])
    bearing = math.radians(float(row['bearing_deg']))
    distance = float(row['range_center_m'])
    center = distance * np.array([math.cos(bearing), math.sin(bearing)])
    roi = np.linalg.norm(points[:, :2] - center, axis=1) < .35
    roi &= np.isfinite(points).all(axis=1) & (height > .03) & (height < .40)
    if not len(times) or not np.isfinite(times).all():
        raise ValueError('Capture must contain finite timestamps')
    bins = np.floor((times - times.min()) / frame_sec).astype(int)
    nframes = int(np.floor((times.max() - times.min()) / frame_sec))
    counts = np.bincount(bins[roi], minlength=nframes)[:nframes]
    kept = roi & (height > .05)
    old = roi & (height > .15)
    return counts, {'roi_points': int(roi.sum()), 'kept_above_5cm': int(kept.sum()),
                    'kept_above_15cm': int(old.sum())}


def compare(row, duration_sec=10., mount_angles=(0., 0., 0.), return_profile='baseline'):
    """Simulate one small-cone setup without fitting to the expected result."""
    config = Mid360Config(return_profile=return_profile)
    mount = MountPose(0., 0., float(row['sensor_height_m']), *mount_angles)
    sensor = Mid360Sensor(config, mount, seed=1)
    bearing = math.radians(float(row['bearing_deg']))
    distance = float(row['range_center_m'])
    cones = np.array([[distance * math.cos(bearing), distance * math.sin(bearing)]])
    counts = []
    for frame in range(int(duration_sec / (1.0 / config.frame_rate_hz))):
        _, on_cone = sensor.frame_labeled(frame * (1.0 / config.frame_rate_hz), 0., 0., 0., cones)
        counts.append(int(on_cone.sum()))
    predicted = availability(counts)
    observed = {'points_per_frame': float(row['pts_per_frame']),
                'frames_hit_pct': float(row['frames_hit_pct'])}
    return {'run': row['run'], 'range_m': distance, 'lighting': row['lighting'],
            'observed': observed, 'simulated': predicted,
            'difference': {key: predicted[key] - value for key, value in observed.items()},
            'mount_angles_rad': list(mount_angles),
            'setup_limit': 'Scan phase and molded profile unknown'}


def fit_overcast(results, duration_sec=10.):
    """Fit small-cone return survival, holding out repeat runs three and four."""
    training, held_out = [], []
    for run in results:
        if not run['run'].startswith('small_out') or 'raw_roi' not in run:
            continue
        if run['run'].endswith(('_run3', '_run4')):
            held_out.append(run)
        else:
            training.append(run)
    if not training:
        raise ValueError('No outdoor raw captures available for profile fitting')
    grouped = {}
    for run in training:
        predicted = run['simulated']['points_per_frame']
        if predicted <= 0:
            continue
        key = round(run['range_m'], 1)
        grouped.setdefault(key, []).append(run['raw_roi']['points_per_frame'] / predicted)
    knots = sorted(grouped)
    survival = [min(1., max(0., float(np.mean(grouped[key])))) for key in knots]
    if not knots:
        raise ValueError('No outdoor captures have geometrically visible simulated cones')
    checks = []
    for run in held_out:
        probability = float(np.interp(run['range_m'], knots, survival))
        predicted = run['simulated']['points_per_frame'] * probability
        observed = run['raw_roi']['points_per_frame']
        checks.append({'run': run['run'], 'observed_points_per_frame': observed,
                       'expected_points_per_frame': predicted,
                       'absolute_error': abs(predicted - observed)})
    return {'profile': 'acceptance_overcast', 'reference_commit': REFERENCE_COMMIT,
            'scope': 'Stationary small cones; overcast daylight at October 4 acceptance setup',
            'baseline_sensor_config': asdict(Mid360Config()), 'seed': 1,
            'simulation_duration_sec': duration_sec,
            'range_m': knots, 'survival_probability': survival,
            'extrapolation': 'Hold endpoint; outside recorded range is unvalidated',
            'training': [{'run': run['run'], 'capture_sha256': run['capture_sha256']}
                         for run in training],
            'held_out': checks, 'scan_phase_known': False,
            'limitations': ['Does not model intensity, sun or non-cone surfaces',
                            'Independent thinning does not fit temporal dropout correlation',
                            'Cone geometry and recorded reference coordinates are approximate']}


def main():
    """Write a JSON comparison; optionally inspect locally downloaded raw captures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path)
    parser.add_argument('--raw-dir', type=Path)
    parser.add_argument('--run', action='append', help='Restrict to named acceptance runs')
    parser.add_argument('--fit-overcast', type=Path,
                        help='Write a small-cone return profile; requires raw recordings')
    parser.add_argument('--return-profile', default='baseline',
                        choices=['baseline', 'acceptance_overcast'])
    parser.add_argument('--duration', type=float, default=10.)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.fit_overcast and args.return_profile != 'baseline':
        parser.error('Profile fitting requires --return-profile baseline')
    if args.fit_overcast and not args.raw_dir:
        parser.error('--fit-overcast requires --raw-dir')
    if args.duration < .1:
        parser.error('--duration must be at least 0.1 seconds')
    reference = args.results or files('lhr_lidar_sim').joinpath('patterns/acceptance_results.csv')
    with reference.open() as stream:
        rows = list(csv.DictReader(stream))
    selected = [row for row in rows if row['run'].startswith('small')
                and row['tilt_setting'] == 'level' and row['range_center_m']
                and row['bearing_deg'] and row['pts_per_frame'] and row['frames_hit_pct']
                and (not args.run or row['run'] in args.run)]
    if not selected:
        parser.error('No measured small-cone level runs match the selection')
    results = []
    for row in selected:
        mount_angles = (0., 0., 0.)
        capture = args.raw_dir / Path(row['raw_npz']).name if args.raw_dir else None
        if capture and capture.exists():
            with np.load(capture, allow_pickle=False) as data:
                rotation = gravity_rotation(data['imu'])
            mount_angles = (math.atan2(rotation[2, 1], rotation[2, 2]),
                            math.asin(float(np.clip(-rotation[2, 0], -1., 1.))),
                            math.atan2(rotation[1, 0], rotation[0, 0]))
        result = compare(row, args.duration, mount_angles, args.return_profile)
        if args.raw_dir:
            if capture.exists():
                counts, heights = capture_counts(capture, row)
                result['raw_roi'] = {**availability(counts), **heights}
                result['capture_sha256'] = hashlib.sha256(capture.read_bytes()).hexdigest()
            else:
                result['raw_missing'] = str(capture)
        results.append(result)
    report = {'reference_commit': None if args.results else REFERENCE_COMMIT,
              'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
              'sensor_config': asdict(Mid360Config(return_profile=args.return_profile)), 'seed': 1,
              'duration_sec': args.duration, 'calibrated': False,
              'scope': 'Stationary return comparison; ROI availability is not detector recall',
              'frame_sec': .1, 'stack_frames': 5, 'minimum_points': 3, 'runs': results}
    if args.fit_overcast:
        profile = fit_overcast(results, args.duration)
        args.fit_overcast.parent.mkdir(parents=True, exist_ok=True)
        args.fit_overcast.write_text(json.dumps(profile, indent=2) + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Wrote {len(results)} comparisons to {args.output}')


if __name__ == '__main__':
    main()
