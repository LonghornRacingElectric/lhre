"""Unit tests for the wheel-speed to body-speed conversion."""

import math

from lhr_state_estimation.wheel_odometry import body_speed


def test_equal_wheels_give_rolling_speed():
    """Convert two equal rear wheel rates into the rolling speed."""
    assert math.isclose(body_speed(10.0, 10.0, 0.2), 2.0)


def test_unequal_wheels_average():
    """Average the unequal rear wheel rates a yaw manoeuvre produces."""
    assert math.isclose(body_speed(8.0, 12.0, 0.25), 2.5)


def test_reverse_keeps_its_sign():
    """Carry the sign through when the wheels turn backwards."""
    assert body_speed(-10.0, -10.0, 0.2) < 0.0
