"""
Extended Kalman filter over the planar vehicle state.

The filter carries ``[x, y, yaw, v, yaw_rate]``: IMU acceleration drives the
prediction, with gyro, optional GNSS position and legacy wheel-speed corrections.
Without GNSS, position is dead reckoned and its uncertainty grows unbounded.

Deliberately free of rclpy so the math runs under plain pytest with no ROS
environment (ADR-009 flags an EKF as the kind of code that earns real unit
tests rather than lint alone).
"""

import math

import numpy as np

IDX_X = 0
IDX_Y = 1
IDX_YAW = 2
IDX_V = 3
IDX_YAW_RATE = 4
STATE_DIM = 5


def wrap_angle(angle: float) -> float:
    """Wrap an angle into [-pi, pi)."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class VehicleEkf:
    """
    Five-state EKF for a planar vehicle.

    The core has no gyro-bias state; startup correction lives in the ROS node.
    A continuous bias state could be constrained without GNSS by
    comparing the gyro with rear-wheel differential yaw rate, and by taking
    zero-rate measurements while stationary. Both need slip and standstill
    checks before adding a bias state.
    """

    def __init__(self, sigma_accel: float = 1.0,
                 sigma_yaw_accel: float = 1.0) -> None:
        self.sigma_accel = sigma_accel
        self.sigma_yaw_accel = sigma_yaw_accel
        self.x = np.zeros(STATE_DIM)
        self.P = self._initial_covariance()

    @staticmethod
    def _initial_covariance() -> np.ndarray:
        return np.diag([1.0, 1.0, 0.1, 0.1, 0.1])

    def reset(self, x: float = 0.0, y: float = 0.0, yaw: float = 0.0) -> None:
        """Re-seed the pose and zero the velocity states."""
        self.x = np.array([x, y, wrap_angle(yaw), 0.0, 0.0], dtype=float)
        self.P = self._initial_covariance()

    @property
    def pose(self) -> tuple:
        """Return the estimated ``(x, y, yaw)``."""
        return (self.x[IDX_X], self.x[IDX_Y], self.x[IDX_YAW])

    @property
    def twist(self) -> tuple:
        """Return the estimated ``(v, yaw_rate)`` in the body frame."""
        return (self.x[IDX_V], self.x[IDX_YAW_RATE])

    def predict(self, dt: float, accel_x: float = 0.0) -> None:
        """Propagate the state forward by ``dt`` seconds."""
        if dt <= 0.0:
            return
        yaw = self.x[IDX_YAW]
        v = self.x[IDX_V]
        yaw_rate = self.x[IDX_YAW_RATE]

        self.x[IDX_X] = self.x[IDX_X] + v * math.cos(yaw) * dt
        self.x[IDX_Y] = self.x[IDX_Y] + v * math.sin(yaw) * dt
        self.x[IDX_YAW] = wrap_angle(yaw + yaw_rate * dt)
        self.x[IDX_V] = v + accel_x * dt

        F = np.eye(STATE_DIM)
        F[IDX_X, IDX_YAW] = -v * math.sin(yaw) * dt
        F[IDX_X, IDX_V] = math.cos(yaw) * dt
        F[IDX_Y, IDX_YAW] = v * math.cos(yaw) * dt
        F[IDX_Y, IDX_V] = math.sin(yaw) * dt
        F[IDX_YAW, IDX_YAW_RATE] = dt

        # Continuous white-acceleration intensity: subdivision of the same
        # interval must not make process uncertainty artificially disappear.
        noise = np.zeros((STATE_DIM, STATE_DIM))
        direction = np.array([math.cos(yaw), math.sin(yaw)])
        intensity = self.sigma_accel**2
        noise[:2, :2] = intensity*dt**3/3 * np.outer(direction, direction)
        noise[:2, IDX_V] = intensity*dt**2/2 * direction
        noise[IDX_V, :2] = noise[:2, IDX_V]
        noise[IDX_V, IDX_V] = intensity*dt
        yaw_intensity = self.sigma_yaw_accel**2
        noise[IDX_YAW, IDX_YAW] = yaw_intensity*dt**3/3
        noise[IDX_YAW, IDX_YAW_RATE] = yaw_intensity*dt**2/2
        noise[IDX_YAW_RATE, IDX_YAW] = noise[IDX_YAW, IDX_YAW_RATE]
        noise[IDX_YAW_RATE, IDX_YAW_RATE] = yaw_intensity*dt
        self.P = F @ self.P @ F.T + noise

    def update_speed(self, speed: float, variance: float) -> None:
        """Correct the longitudinal speed from a wheel-derived measurement."""
        self._update_scalar(IDX_V, speed, variance)

    def update_stationary_speed(self, variance: float) -> None:
        """Fuse independently verified zero speed without changing gyro heading."""
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError('stationary speed variance must be positive and finite')
        self._update_scalar(IDX_V, 0., variance, heading_gain=0.)

    def update_yaw_rate(self, yaw_rate: float, variance: float) -> None:
        """Correct the yaw rate from a gyro measurement."""
        self._update_scalar(IDX_YAW_RATE, yaw_rate, variance)

    def update_position(self, position, covariance, lever=(0., 0.), gate=25., heading_gain=1.):
        """Fuse antenna position, optionally attenuating both angular gain rows."""
        if not math.isfinite(heading_gain) or not 0 <= heading_gain <= 1:
            raise ValueError('heading_gain must be finite and within [0, 1]')
        z, r = np.asarray(position, dtype=float), np.asarray(covariance, dtype=float)
        if (z.shape != (2,) or r.shape != (2, 2) or not np.isfinite(z).all()
                or not np.isfinite(r).all() or not np.allclose(r, r.T)
                or np.linalg.eigvalsh(r).min() <= 0):
            raise ValueError('Require finite position and positive definite 2D covariance')
        lx, ly = lever[:2]
        yaw = self.x[IDX_YAW]
        c, s = math.cos(yaw), math.sin(yaw)
        predicted = self.x[:2] + [c*lx-s*ly, s*lx+c*ly]
        h = np.zeros((2, STATE_DIM))
        h[0, IDX_X], h[1, IDX_Y] = 1, 1
        h[:, IDX_YAW] = [-s*lx-c*ly, c*lx-s*ly]
        residual = z-predicted
        innovation = h @ self.P @ h.T+r
        nis = float(residual @ np.linalg.solve(innovation, residual))
        if nis > gate:
            return False, nis
        gain = np.linalg.solve(innovation, h @ self.P).T
        # Position/speed rows keep their gain. Angular rows and Joseph covariance
        # use the same modified gain; overwriting yaw afterward would be inconsistent.
        gain[[IDX_YAW, IDX_YAW_RATE], :] *= heading_gain
        self.x += gain @ residual
        self.x[IDX_YAW] = wrap_angle(self.x[IDX_YAW])
        ikh = np.eye(STATE_DIM)-gain @ h
        self.P = ikh @ self.P @ ikh.T+gain @ r @ gain.T
        self.P = .5*(self.P+self.P.T)
        return True, nis

    def _update_scalar(self, index: int, measurement: float,
                       variance: float, heading_gain: float = 1.) -> None:
        H = np.zeros((1, STATE_DIM))
        H[0, index] = 1.0
        innovation = measurement - self.x[index]
        S = (H @ self.P @ H.T).item() + variance
        K = (self.P @ H.T) / S
        K[[IDX_YAW, IDX_YAW_RATE], :] *= heading_gain

        self.x = self.x + (K * innovation).ravel()
        self.x[IDX_YAW] = wrap_angle(self.x[IDX_YAW])

        # Joseph form: stays symmetric positive-definite under bad tuning.
        I_KH = np.eye(STATE_DIM) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + (K @ K.T) * variance
        self.P = 0.5 * (self.P + self.P.T)
