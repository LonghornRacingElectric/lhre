"""Check skidpad circles and the unobstructed central entry/exit lane."""

import math

from lhr_trackgen.publish_cones import generate_skidpad_track, GENERATORS
import pytest


def test_skidpad_rules_geometry():
    left, right = GENERATORS['skidpad'](seed=1)
    assert len(left) == len(right) == 30
    # Blue is inner on the left circle and outer on the right; yellow
    # is the opposite, matching the prescribed direction on each circle.
    for points, cx, radius, count in (
        (left[:17], -9.125, 7.625, 17),
        (right[:13], -9.125, 10.625, 13),
        (right[13:], 9.125, 7.625, 17),
        (left[17:], 9.125, 10.625, 13),
    ):
        assert len(points) == count
        for x, y in points:
            assert math.hypot(x - cx, y) == pytest.approx(radius)
    assert len(set(left + right)) == 60
    assert all(abs(x) >= 1.5 - 1e-9 for x, _ in left + right)
    assert (-1.5, 0.0) in left
    assert right[13] == pytest.approx((1.5, 0.0))


def test_skidpad_is_fixed_rules_layout():
    assert generate_skidpad_track(seed=1) == generate_skidpad_track(
        seed=42, radius_m=100.0, width_m=8.0, jitter_m=10.0)
