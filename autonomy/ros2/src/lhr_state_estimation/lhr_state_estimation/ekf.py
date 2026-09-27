"""
Extended Kalman filter over the planar vehicle state.

The filter carries ``[x, y, yaw, v, yaw_rate]``: IMU acceleration drives the
prediction, and the gyro and the wheel-derived speed correct it. There is no
absolute position measurement yet, so position is dead reckoned and its
covariance grows without bound until GNSS lands.

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

    Gyro bias is deliberately *not* estimated: with no absolute heading
    reference (no GNSS, no magnetometer) it is unobservable, so a bias state
    would only random-walk. Add it with the GNSS work, not before.
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

        # White acceleration noise, mapped into every state it touches over dt.
        G = np.zeros((STATE_DIM, 2))
        G[IDX_X, 0] = 0.5 * math.cos(yaw) * dt * dt
        G[IDX_Y, 0] = 0.5 * math.sin(yaw) * dt * dt
        G[IDX_V, 0] = dt
        G[IDX_YAW, 1] = 0.5 * dt * dt
        G[IDX_YAW_RATE, 1] = dt
        noise = np.diag([self.sigma_accel ** 2, self.sigma_yaw_accel ** 2])

        self.P = F @ self.P @ F.T + G @ noise @ G.T

    def update_speed(self, speed: float, variance: float) -> None:
        """Correct the longitudinal speed from a wheel-derived measurement."""
        self._update_scalar(IDX_V, speed, variance)

    def update_yaw_rate(self, yaw_rate: float, variance: float) -> None:
        """Correct the yaw rate from a gyro measurement."""
        self._update_scalar(IDX_YAW_RATE, yaw_rate, variance)

    def _update_scalar(self, index: int, measurement: float,
                       variance: float) -> None:
        H = np.zeros((1, STATE_DIM))
        H[0, index] = 1.0
        innovation = measurement - self.x[index]
        S = (H @ self.P @ H.T).item() + variance
        K = (self.P @ H.T) / S

        self.x = self.x + (K * innovation).ravel()
        self.x[IDX_YAW] = wrap_angle(self.x[IDX_YAW])

        # Joseph form: stays symmetric positive-definite under bad tuning.
        I_KH = np.eye(STATE_DIM) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + (K @ K.T) * variance
        self.P = 0.5 * (self.P + self.P.T)
