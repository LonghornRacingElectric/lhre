"""Bounded startup gyro-z calibration with independent raw-LiDAR motion evidence."""

from collections import deque
import math

import numpy as np
from scipy.spatial import cKDTree

CALIBRATION_DEFAULTS = {
    'gyro_calibration_duration_s': 3.,
    'gyro_calibration_settle_s': 2.,
    'gyro_calibration_timeout_s': 15.,
    'gyro_calibration_max_gap_s': .25,
    'gyro_calibration_max_translation_m': .03,
    'gyro_calibration_max_yaw_deg': .15,
    'gyro_calibration_max_gyro_std_rad_s': .01,
    'gyro_calibration_max_accel_std_m_s2': .15,
    'gyro_calibration_max_gyro_mean_rad_s': .03,
    'gyro_calibration_min_samples': 200,
    'gyro_calibration_min_lidar_points': 30,
    'gyro_calibration_lidar_topic': '/lhr/lidar/points',
    'gyro_calibration_lidar_qos': 'reliable',
}


def scan_motion(reference, current):
    """Fit planar rigid motion; reject insufficient overlap or degenerate geometry."""
    reference, current = np.asarray(reference), np.asarray(current)
    if min(len(reference), len(current)) < 20:
        return None
    if min(np.linalg.eigvalsh(np.cov(p.T)).min() for p in [reference, current]) < .1:
        return None
    tree = cKDTree(reference)
    rotation, translation = np.eye(2), np.zeros(2)
    for _ in range(15):
        mapped = current @ rotation.T+translation
        distances, indices = tree.query(mapped)
        valid = distances < .3
        if valid.mean() < .7 or valid.sum() < 20:
            return None
        a, b = current[valid], reference[indices[valid]]
        ac, bc = a.mean(axis=0), b.mean(axis=0)
        u, _, vt = np.linalg.svd((a-ac).T @ (b-bc))
        next_rotation = vt.T @ u.T
        if np.linalg.det(next_rotation) < 0:
            vt[-1] *= -1
            next_rotation = vt.T @ u.T
        next_translation = bc-next_rotation @ ac
        converged = (np.linalg.norm(next_translation-translation) < 1e-6
                     and np.linalg.norm(next_rotation-rotation) < 1e-6)
        rotation, translation = next_rotation, next_translation
        if converged:
            break
    distances, _ = tree.query(current @ rotation.T+translation)
    if np.percentile(distances, 90) > .08:
        return None
    return float(np.linalg.norm(translation)), abs(math.atan2(rotation[1, 0], rotation[0, 0]))


class GyroCalibration:
    """Estimate one offset only after a contiguous, sufficiently still sensor window."""

    def __init__(self, values):
        self.config = {**CALIBRATION_DEFAULTS, **values}
        for name, value in self.config.items():
            if name.endswith('_qos'):
                if value not in ('reliable', 'sensor_data'):
                    raise ValueError(f'{name} must be reliable or sensor_data')
                continue
            if name.endswith('_topic'):
                if not isinstance(value, str) or not value:
                    raise ValueError(f'{name} must be a nonempty topic')
                continue
            if not math.isfinite(value) or value < 0 or (value == 0 and 'settle' not in name):
                raise ValueError(f'{name} must be positive and finite (settle may be zero)')
        if self.get('timeout_s') <= self.get('settle_s')+self.get('duration_s'):
            raise ValueError('gyro_calibration_timeout_s must exceed settle plus duration')
        for name, minimum in [('min_samples', 2), ('min_lidar_points', 20)]:
            value = self.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError(f'gyro_calibration_{name} must be an integer >= {minimum}')
        self.start_ns = None
        self.anchor_ns = None
        self.anchor = None
        self.last_imu_ns = None
        self.last_cloud_ns = None
        self.imu = deque()
        self.state, self.reason = 'waiting', 'sensors'
        self.bias_z = 0.
        self.result = {}
        self.max_translation = self.max_yaw = 0.
        self.clouds = 0
        self.restarts = 0
        self.last_restart_reason = None

    def get(self, name):
        return self.config['gyro_calibration_'+name]

    @property
    def ready(self):
        return self.state == 'ready'

    def fail(self, reason):
        self.state, self.reason = 'failed', reason

    def restart_window(self, reason):
        self.anchor = self.anchor_ns = self.last_cloud_ns = None
        self.clouds = 0
        self.max_translation = self.max_yaw = 0.
        self.state, self.reason = 'waiting', reason
        self.restarts += 1
        self.last_restart_reason = reason

    def tick(self, stamp_ns):
        if self.state in ('ready', 'failed') or stamp_ns <= 0:
            return
        if self.start_ns is None:
            self.start_ns = stamp_ns
        if stamp_ns-self.start_ns >= round(self.get('timeout_s')*1e9):
            self.fail('timeout')

    def add_imu(self, stamp_ns, gyro, accel):
        if self.state in ('ready', 'failed') or stamp_ns <= 0:
            return
        if self.last_imu_ns is not None and stamp_ns <= self.last_imu_ns:
            return
        self.tick(stamp_ns)
        if self.state == 'failed':
            return
        if not np.isfinite([*gyro, *accel]).all():
            self.fail('nonfinite_imu')
            return
        if (self.anchor_ns is not None and self.last_imu_ns is not None
                and (stamp_ns-self.last_imu_ns)*1e-9 > self.get('max_gap_s')):
            self.restart_window('imu_gap')
        self.last_imu_ns = stamp_ns
        self.imu.append((stamp_ns, *gyro, *accel))
        while self.imu and stamp_ns-self.imu[0][0] > round((self.get('duration_s')+1)*1e9):
            self.imu.popleft()
        self._finish()

    def add_cloud(self, stamp_ns, points):
        if self.state in ('ready', 'failed') or stamp_ns <= 0:
            return
        if self.last_cloud_ns is not None and stamp_ns <= self.last_cloud_ns:
            return
        self.tick(stamp_ns)
        if self.state == 'failed':
            return
        if (stamp_ns-self.start_ns)*1e-9 < self.get('settle_s'):
            return
        points = np.asarray(points).reshape(-1, 2)
        points = points[np.isfinite(points).all(axis=1)]
        # Limit density without giving a dense nearby object all registration weight.
        _, indices = np.unique(np.floor(points/.08).astype(np.int64), axis=0, return_index=True)
        points = points[np.sort(indices)]
        if len(points) < self.get('min_lidar_points'):
            self.reason = 'insufficient_lidar_geometry'
            return
        if self.anchor is None:
            if scan_motion(points, points) is None:
                self.reason = 'unobservable_lidar_geometry'
                return
            self.anchor, self.anchor_ns = points, stamp_ns
            self.state, self.reason = 'collecting', 'stationary_window'
        else:
            if (stamp_ns-self.last_cloud_ns)*1e-9 > self.get('max_gap_s'):
                self.restart_window('lidar_gap')
                self.add_cloud(stamp_ns, points)
                return
            motion = scan_motion(self.anchor, points)
            if motion is None:
                self.fail('lidar_registration_quality')
                return
            translation, yaw = motion
            self.max_translation = max(self.max_translation, translation)
            self.max_yaw = max(self.max_yaw, yaw)
            if (translation > self.get('max_translation_m')
                    or math.degrees(yaw) > self.get('max_yaw_deg')):
                self.fail('lidar_motion')
                return
        self.clouds += 1
        self.last_cloud_ns = stamp_ns
        self._finish()

    def _finish(self):
        if self.anchor_ns is None or self.last_imu_ns is None or self.last_cloud_ns is None:
            return
        if self.state == 'failed':
            return
        if self.clouds < 3:
            return
        end = min(self.last_imu_ns, self.last_cloud_ns)
        if abs(self.last_imu_ns-self.last_cloud_ns)*1e-9 > self.get('max_gap_s'):
            return
        if (end-self.anchor_ns)*1e-9 < self.get('duration_s'):
            return
        samples = np.asarray([r for r in self.imu if self.anchor_ns <= r[0] <= end])
        if len(samples) < self.get('min_samples'):
            self.fail('insufficient_imu_samples')
            return
        if (samples[-1, 0]-samples[0, 0])*1e-9 < self.get('duration_s')-.01:
            self.fail('insufficient_imu_coverage')
            return
        gyro, accel = samples[:, 1:4], samples[:, 4:7]
        if np.max(gyro.std(axis=0, ddof=1)) > self.get('max_gyro_std_rad_s'):
            self.fail('gyro_not_quiet')
            return
        if np.max(accel.std(axis=0, ddof=1)) > self.get('max_accel_std_m_s2'):
            self.fail('accel_not_quiet')
            return
        if np.max(abs(gyro.mean(axis=0))) > self.get('max_gyro_mean_rad_s'):
            self.fail('gyro_mean_too_large')
            return
        self.bias_z = float(gyro[:, 2].mean())
        self.result = {
            'bias_z_rad_s': self.bias_z, 'samples': len(samples), 'clouds': self.clouds,
            'window_start_s': self.anchor_ns*1e-9, 'window_end_s': end*1e-9,
            'gyro_std_rad_s': gyro.std(axis=0, ddof=1).tolist(),
            'accel_std_m_s2': accel.std(axis=0, ddof=1).tolist(),
            'max_lidar_translation_m': self.max_translation,
            'max_lidar_yaw_deg': math.degrees(self.max_yaw),
        }
        self.state, self.reason = 'ready', 'stationary_window_verified'

    def status(self):
        return {'state': self.state, 'reason': self.reason, 'restarts': self.restarts,
                'last_restart_reason': self.last_restart_reason,
                'last_imu_s': None if self.last_imu_ns is None else self.last_imu_ns*1e-9,
                'last_cloud_s': None if self.last_cloud_ns is None else self.last_cloud_ns*1e-9,
                **self.result}
