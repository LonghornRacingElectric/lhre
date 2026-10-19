"""Unit tests for the EKF math. No ROS environment needed."""

import math

from lhr_state_estimation.ekf import IDX_V, IDX_X, STATE_DIM, VehicleEkf, wrap_angle
import numpy as np

TIGHT = 1e-6


def _converged_filter(speed, yaw_rate):
    """Build a filter whose velocity states already track the given motion."""
    ekf = VehicleEkf()
    ekf.reset()
    for _ in range(20):
        ekf.update_speed(speed, TIGHT)
        ekf.update_yaw_rate(yaw_rate, TIGHT)
    return ekf


def test_wrap_angle_folds_into_range():
    """Wrap angles outside [-pi, pi) back into it."""
    assert wrap_angle(0.0) == 0.0
    assert math.isclose(wrap_angle(3.0 * math.pi), -math.pi, abs_tol=1e-9)
    assert -math.pi <= wrap_angle(100.0) < math.pi


def test_straight_line_dead_reckoning():
    """Integrate a straight run at constant speed to the analytic distance."""
    ekf = _converged_filter(speed=5.0, yaw_rate=0.0)
    for _ in range(100):
        ekf.predict(0.01)
        ekf.update_speed(5.0, TIGHT)
        ekf.update_yaw_rate(0.0, TIGHT)

    x, y, yaw = ekf.pose
    assert math.isclose(x, 5.0, abs_tol=0.02)
    assert math.isclose(y, 0.0, abs_tol=1e-6)
    assert math.isclose(yaw, 0.0, abs_tol=1e-9)


def test_circle_returns_to_start():
    """Close a full noise-free circle back onto the starting point."""
    speed, yaw_rate, dt = 5.0, 0.5, 0.01
    ekf = _converged_filter(speed, yaw_rate)

    steps = int(round((2.0 * math.pi / yaw_rate) / dt))
    for _ in range(steps):
        ekf.predict(dt)
        ekf.update_speed(speed, TIGHT)
        ekf.update_yaw_rate(yaw_rate, TIGHT)

    x, y, _ = ekf.pose
    assert math.hypot(x, y) < 0.5


def test_speed_update_pulls_state_and_shrinks_covariance():
    """Drive the speed state toward a measurement and reduce its variance."""
    ekf = VehicleEkf()
    ekf.reset()
    before = ekf.P[IDX_V, IDX_V]

    ekf.update_speed(4.0, 0.01)

    assert ekf.x[IDX_V] > 3.0
    assert ekf.P[IDX_V, IDX_V] < before


def test_position_covariance_grows_while_dead_reckoning():
    """Grow position uncertainty without bound when nothing measures it."""
    ekf = _converged_filter(speed=5.0, yaw_rate=0.0)
    start = ekf.P[IDX_X, IDX_X]

    for _ in range(200):
        ekf.predict(0.01)
        ekf.update_speed(5.0, TIGHT)
        ekf.update_yaw_rate(0.0, TIGHT)

    assert ekf.P[IDX_X, IDX_X] > start


def test_covariance_stays_symmetric_and_finite():
    """Keep the covariance symmetric and finite through many updates."""
    ekf = _converged_filter(speed=3.0, yaw_rate=0.2)
    for _ in range(500):
        ekf.predict(0.02, accel_x=0.1)
        ekf.update_speed(3.0, 0.04)
        ekf.update_yaw_rate(0.2, 0.01)

    assert ekf.P.shape == (STATE_DIM, STATE_DIM)
    assert np.all(np.isfinite(ekf.P))
    assert np.allclose(ekf.P, ekf.P.T, atol=1e-9)


def test_noisy_sensors_track_a_circle():
    """Hold dead-reckoning drift to a few metres on a noisy 60 s circle."""
    rng = np.random.default_rng(7)
    speed, yaw_rate, dt = 6.0, 0.4, 0.02
    gyro_std, accel_std, speed_std = 0.01, 0.15, 0.02

    ekf = _converged_filter(speed, yaw_rate)
    truth_x, truth_y, truth_yaw = 0.0, 0.0, 0.0

    for _ in range(int(60.0 / dt)):
        truth_x += speed * math.cos(truth_yaw) * dt
        truth_y += speed * math.sin(truth_yaw) * dt
        truth_yaw = wrap_angle(truth_yaw + yaw_rate * dt)

        ekf.predict(dt, accel_x=rng.normal(0.0, accel_std))
        ekf.update_speed(speed + rng.normal(0.0, speed_std), speed_std ** 2)
        ekf.update_yaw_rate(yaw_rate + rng.normal(0.0, gyro_std), gyro_std ** 2)

    x, y, _ = ekf.pose
    assert math.hypot(x - truth_x, y - truth_y) < 3.0
