"""Experimental standstill evidence from raw scans and calibrated IMU samples."""

from collections import deque
import math

import numpy as np

from .gyro_calibration import scan_motion

STATIONARY_DEFAULTS = {
    'stationary_duration_s': 3.,
    'stationary_max_gap_s': .25,
    'stationary_max_translation_m': .03,
    'stationary_max_yaw_deg': .15,
    'stationary_max_gyro_rad_s': .02,
    'stationary_max_accel_std_m_s2': .15,
    'stationary_speed_variance': .0025,
}


class StationaryEvidence:
    """Require a full quiet window; retain its scan anchor until evidence fails."""

    def __init__(self, values):
        self.config = {**STATIONARY_DEFAULTS, **values}
        for name, value in self.config.items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        if self.get('duration_s') <= self.get('max_gap_s'):
            raise ValueError('stationary_duration_s must exceed stationary_max_gap_s')
        self.imu = deque()
        self.last_imu_ns = self.last_cloud_ns = None
        self.anchor = self.anchor_ns = None
        self.ready = False
        self.reason = 'collecting'

    def get(self, name):
        return self.config['stationary_'+name]

    def reset(self, reason):
        self.anchor = self.anchor_ns = None
        self.ready, self.reason = False, reason

    def add_imu(self, stamp_ns, gyro, accel):
        if self.last_imu_ns is not None and stamp_ns <= self.last_imu_ns:
            return
        if (self.last_imu_ns is not None
                and (stamp_ns-self.last_imu_ns)*1e-9 > self.get('max_gap_s')):
            self.reset('imu_gap')
            self.imu.clear()
        self.last_imu_ns = stamp_ns
        values = np.asarray([*gyro, *accel], dtype=float)
        if not np.isfinite(values).all():
            self.reset('invalid_imu')
            self.imu.clear()
            return
        # Quiet acceleration alone cannot distinguish rest from coasting.
        # It can, however, promptly revoke scan evidence after a launch/impact.
        if (np.max(abs(values[:3])) > self.get('max_gyro_rad_s')
                or (self.imu and np.max(abs(values[3:]-self.imu[-1][1][3:]))
                    > 3*self.get('max_accel_std_m_s2'))):
            self.reset('imu_motion')
            self.imu.clear()
        self.imu.append((stamp_ns, values))
        retention_ns = round((self.get('duration_s')+self.get('max_gap_s'))*1e9)
        while self.imu and stamp_ns-self.imu[0][0] > retention_ns:
            self.imu.popleft()
        self.fresh(stamp_ns)

    def fresh(self, stamp_ns):
        if (self.last_cloud_ns is None or self.last_imu_ns is None
                or max(abs(stamp_ns-self.last_cloud_ns), abs(stamp_ns-self.last_imu_ns))
                > round(self.get('max_gap_s')*1e9)):
            self.reset('sensor_gap')
        return self.ready

    def add_cloud(self, stamp_ns, points):
        if self.last_cloud_ns is not None and stamp_ns <= self.last_cloud_ns:
            self.reset('unordered_cloud')
            return False
        self.fresh(stamp_ns)
        self.last_cloud_ns = stamp_ns
        points = np.asarray(points).reshape(-1, 2)
        points = points[np.isfinite(points).all(axis=1)]
        _, indices = np.unique(np.floor(points/.08).astype(np.int64),
                               axis=0, return_index=True)
        points = points[np.sort(indices)]
        if len(points) < 30 or scan_motion(points, points) is None:
            self.reset('unobservable_scene')
            return False
        if self.anchor is None:
            self.anchor, self.anchor_ns = points, stamp_ns
            self.reason = 'collecting'
            return False
        motion = scan_motion(self.anchor, points)
        if (motion is None or motion[0] > self.get('max_translation_m')
                or math.degrees(motion[1]) > self.get('max_yaw_deg')):
            self.reset('lidar_motion_or_registration')
            return False
        start = stamp_ns-round(self.get('duration_s')*1e9)
        samples = [v for t, v in self.imu if start <= t <= stamp_ns]
        stamps = [t for t, _ in self.imu if start <= t <= stamp_ns]
        if (len(samples) < 2 or stamp_ns-self.anchor_ns < round(self.get('duration_s')*1e9)
                or (stamps[-1]-stamps[0])*1e-9 < self.get('duration_s')-.02):
            return False
        values = np.asarray(samples)
        if (np.max(abs(values[:, :3])) > self.get('max_gyro_rad_s')
                or np.max(values[:, 3:].std(axis=0, ddof=1))
                > self.get('max_accel_std_m_s2')):
            self.reset('imu_not_quiet')
            return False
        self.ready, self.reason = True, 'stationary_verified'
        return self.fresh(stamp_ns)
