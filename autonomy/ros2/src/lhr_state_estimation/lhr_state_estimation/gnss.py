"""WGS84 local ENU conversion and bounded delayed-position correction history."""

from dataclasses import dataclass
import math

import numpy as np

from .ekf import wrap_angle

WGS84_A = 6378137.0
WGS84_F = 1/298.257223563
WGS84_E2 = WGS84_F*(2-WGS84_F)


def ecef(latitude, longitude, altitude):
    """Convert geodetic degrees and ellipsoid height to Earth-centered metres."""
    if not all(math.isfinite(v) for v in (latitude, longitude, altitude)):
        raise ValueError('GNSS coordinates must be finite')
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('GNSS latitude/longitude out of range')
    lat, lon = math.radians(latitude), math.radians(longitude)
    n = WGS84_A/math.sqrt(1-WGS84_E2*math.sin(lat)**2)
    return np.array([(n+altitude)*math.cos(lat)*math.cos(lon),
                     (n+altitude)*math.cos(lat)*math.sin(lon),
                     (n*(1-WGS84_E2)+altitude)*math.sin(lat)])


class LocalENU:
    """A fixed surveyed datum; map x=east, y=north, z=up."""

    def __init__(self, latitude, longitude, altitude):
        self.altitude = altitude
        self.origin = ecef(latitude, longitude, altitude)
        lat, lon = math.radians(latitude), math.radians(longitude)
        self.rotation = np.array([
            [-math.sin(lon), math.cos(lon), 0],
            [-math.sin(lat)*math.cos(lon), -math.sin(lat)*math.sin(lon), math.cos(lat)],
            [math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat)],
        ])

    def to_enu(self, latitude, longitude, altitude):
        return self.rotation @ (ecef(latitude, longitude, altitude)-self.origin)

    def to_geodetic(self, east, north, up):
        x, y, z = self.origin+self.rotation.T @ np.array([east, north, up])
        b = WGS84_A*(1-WGS84_F)
        p = math.hypot(x, y)
        theta = math.atan2(z*WGS84_A, p*b)
        ep2 = (WGS84_A**2-b**2)/b**2
        lat = math.atan2(z+ep2*b*math.sin(theta)**3,
                         p-WGS84_E2*WGS84_A*math.cos(theta)**3)
        n = WGS84_A/math.sqrt(1-WGS84_E2*math.sin(lat)**2)
        altitude = p/math.cos(lat)-n if p > 1e-8 else abs(z)-b
        return math.degrees(lat), math.degrees(math.atan2(y, x)), altitude


@dataclass
class Checkpoint:
    stamp_ns: int
    accel: float
    gyro: float | None
    gyro_variance: float
    state: np.ndarray
    covariance: np.ndarray
    speed_variance: float | None = None


class PositionHistory:
    """Rewind to an ordered GNSS timestamp, correct, then replay later IMUs."""

    def __init__(self, ekf, duration_s=1.0):
        if not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError('GNSS history duration must be positive and finite')
        self.ekf = ekf
        self.duration_ns = int(duration_s*1e9)
        self.records = []
        self.last_fix_ns = -1
        self.last_speed_ns = -1
        self.last_correction = {}

    def record(self, stamp_ns, accel, gyro, variance, speed_variance=None):
        self.records.append(Checkpoint(stamp_ns, accel, gyro, variance,
                                       self.ekf.x.copy(), self.ekf.P.copy(), speed_variance))
        while len(self.records) > 2 and self.records[1].stamp_ns < stamp_ns-self.duration_ns:
            self.records.pop(0)

    def correct(self, stamp_ns, position, covariance, lever, gate, heading_gain=1.):
        if not math.isfinite(heading_gain) or not 0 <= heading_gain <= 1:
            raise ValueError('heading_gain must be finite and within [0, 1]')
        self.last_correction = {}
        if (not self.records or stamp_ns < self.records[0].stamp_ns
                or stamp_ns <= self.last_fix_ns):
            return False, 'old_or_duplicate', None
        if stamp_ns > self.records[-1].stamp_ns:
            return False, 'future', None
        index = max(i for i, r in enumerate(self.records) if r.stamp_ns <= stamp_ns)
        anchor = self.records[index]
        saved_x, saved_p = self.ekf.x.copy(), self.ekf.P.copy()
        self.ekf.x, self.ekf.P = anchor.state.copy(), anchor.covariance.copy()
        accel = anchor.accel
        if stamp_ns > anchor.stamp_ns:
            following = self.records[index+1]
            fraction = (stamp_ns-anchor.stamp_ns)/(following.stamp_ns-anchor.stamp_ns)
            accel += fraction*(following.accel-anchor.accel)
            self.ekf.predict((stamp_ns-anchor.stamp_ns)*1e-9, .5*(anchor.accel+accel))
        before_fix = self.ekf.x.copy()
        cross_covariance = self.ekf.P[2, :2].copy()
        accepted, innovation = self.ekf.update_position(
            position, covariance, lever, gate, heading_gain)
        self.last_correction = {
            'fix_yaw_before_rad': float(before_fix[2]),
            'fix_yaw_after_rad': float(self.ekf.x[2]),
            'fix_yaw_delta_rad': float(wrap_angle(self.ekf.x[2]-before_fix[2])),
            'fix_speed_before_m_s': float(before_fix[3]),
            'fix_speed_after_m_s': float(self.ekf.x[3]),
            'fix_yaw_position_cross_covariance': cross_covariance.tolist(),
            'heading_gain': float(heading_gain),
        }
        if not accepted:
            self.ekf.x, self.ekf.P = saved_x, saved_p
            return False, 'innovation_gate', innovation
        later = self.records[index+1:]
        self.records = self.records[:index+1]
        self.record(stamp_ns, accel, None, 0.)
        previous = stamp_ns
        for old in later:
            self.ekf.predict((old.stamp_ns-previous)*1e-9, .5*(accel+old.accel))
            if old.gyro is not None:
                self.ekf.update_yaw_rate(old.gyro, old.gyro_variance)
            if old.speed_variance is not None:
                self.ekf.update_stationary_speed(old.speed_variance)
            self.record(old.stamp_ns, old.accel, old.gyro, old.gyro_variance,
                        old.speed_variance)
            previous, accel = old.stamp_ns, old.accel
        self.last_fix_ns = stamp_ns
        self.last_correction.update({
            'current_yaw_before_rad': float(saved_x[2]),
            'current_yaw_after_rad': float(self.ekf.x[2]),
            'current_yaw_delta_rad': float(wrap_angle(self.ekf.x[2]-saved_x[2])),
            'current_speed_before_m_s': float(saved_x[3]),
            'current_speed_after_m_s': float(self.ekf.x[3]),
        })
        return True, 'fused', innovation

    def correct_stationary_speed(self, stamp_ns, variance):
        """Insert scan-timed zero speed, retaining it for subsequent GPS replay."""
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError('stationary speed variance must be positive and finite')
        # A scan older than an already fused fix would erase that fix on rewind.
        # Reject it rather than claiming unsupported arbitrary event ordering.
        if (not self.records or stamp_ns < self.records[0].stamp_ns
                or stamp_ns < self.last_fix_ns or stamp_ns <= self.last_speed_ns
                or stamp_ns > self.records[-1].stamp_ns):
            return False
        index = max(i for i, r in enumerate(self.records) if r.stamp_ns <= stamp_ns)
        anchor = self.records[index]
        self.ekf.x, self.ekf.P = anchor.state.copy(), anchor.covariance.copy()
        accel = anchor.accel
        if stamp_ns > anchor.stamp_ns:
            following = self.records[index+1]
            fraction = (stamp_ns-anchor.stamp_ns)/(following.stamp_ns-anchor.stamp_ns)
            accel += fraction*(following.accel-anchor.accel)
            self.ekf.predict((stamp_ns-anchor.stamp_ns)*1e-9, .5*(anchor.accel+accel))
        self.ekf.update_stationary_speed(variance)
        later = self.records[index+1:]
        self.records = self.records[:index+1]
        self.record(stamp_ns, accel, None, 0., variance)
        previous = stamp_ns
        for old in later:
            self.ekf.predict((old.stamp_ns-previous)*1e-9, .5*(accel+old.accel))
            if old.gyro is not None:
                self.ekf.update_yaw_rate(old.gyro, old.gyro_variance)
            if old.speed_variance is not None:
                self.ekf.update_stationary_speed(old.speed_variance)
            self.record(old.stamp_ns, old.accel, old.gyro, old.gyro_variance,
                        old.speed_variance)
            previous, accel = old.stamp_ns, old.accel
        self.last_speed_ns = stamp_ns
        return True
