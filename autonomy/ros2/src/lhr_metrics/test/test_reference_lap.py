"""The reference lap matches closed-form answers and responds to each limit the right way."""

import math

from lhr_metrics.reference_lap import reference_lap
import pytest

LIMITS = {'v_max': 5.0, 'v_min': 1.0, 'a_lat_max': 3.0, 'max_accel': 1.0, 'max_decel': 2.0}


def circle(radius, vertices=720):
    return [(radius * math.cos(2 * math.pi * i / vertices),
             radius * math.sin(2 * math.pi * i / vertices)) for i in range(vertices)]


def stadium(straight=40.0, radius=5.0, step=0.25):
    """Two straights joined by semicircles, counterclockwise from the bottom straight."""
    points = []
    for i in range(int(straight / step)):
        points.append((-straight / 2 + i * step, -radius))
    arc = int(math.pi * radius / step)
    for i in range(arc):
        a = -math.pi / 2 + math.pi * i / arc
        points.append((straight / 2 + radius * math.cos(a), radius * math.sin(a)))
    for i in range(int(straight / step)):
        points.append((straight / 2 - i * step, radius))
    for i in range(arc):
        a = math.pi / 2 + math.pi * i / arc
        points.append((-straight / 2 + radius * math.cos(a), radius * math.sin(a)))
    return points


def standing_start_circle(radius, v):
    """Accelerate from rest at max_accel to v, then hold it for the rest of the circle."""
    a = LIMITS['max_accel']
    return v / a + (2 * math.pi * radius - v * v / (2 * a)) / v


def test_circle_limited_by_v_max():
    expected = standing_start_circle(20.0, LIMITS['v_max'])
    got = reference_lap(circle(20.0), start=(20.0, 0.0), tangent=(0.0, 1.0), **LIMITS)
    assert got == pytest.approx(expected, rel=5e-4)


def test_circle_limited_by_lateral_acceleration():
    corner = math.sqrt(LIMITS['a_lat_max'] * 5.0)
    assert corner < LIMITS['v_max']
    expected = standing_start_circle(5.0, corner)
    got = reference_lap(circle(5.0), start=(5.0, 0.0), tangent=(0.0, 1.0), **LIMITS)
    assert got == pytest.approx(expected, rel=5e-4)


def test_start_point_and_direction_do_not_matter_on_a_circle():
    times = [reference_lap(circle(20.0), start=start, tangent=tangent, **LIMITS)
             for start, tangent in (((20.0, 0.0), (0.0, 1.0)), ((0.0, 20.0), (-1.0, 0.0)),
                                    ((20.0, 0.0), (0.0, -1.0)))]
    assert max(times) - min(times) < 1e-6


def test_v_min_floors_a_corner_tighter_than_a_lat_allows():
    # sqrt(0.3 * 5) is 1.22 m/s, under v_min: the planner never goes slower than v_min.
    tight = dict(LIMITS, a_lat_max=0.3, v_min=2.0)
    expected = standing_start_circle(5.0, 2.0)
    got = reference_lap(circle(5.0), start=(5.0, 0.0), tangent=(0.0, 1.0), **tight)
    assert got == pytest.approx(expected, rel=5e-4)


def test_each_limit_only_ever_speeds_the_lap_up():
    def lap(**changes):
        return reference_lap(stadium(), start=(0.0, -5.0), tangent=(1.0, 0.0),
                             **dict(LIMITS, **changes))
    base = lap()
    for name, value in (('v_max', 8.0), ('a_lat_max', 6.0), ('max_accel', 3.0),
                        ('max_decel', 6.0)):
        assert lap(**{name: value}) < base, name


def test_braking_into_corners_is_counted():
    # With almost no braking the car would reach v_max on each straight and
    # stand on the brakes at the turn-in; a real decel limit costs time.
    free = reference_lap(stadium(), start=(0.0, -5.0), tangent=(1.0, 0.0),
                         **dict(LIMITS, max_decel=1e6))
    assert reference_lap(stadium(), start=(0.0, -5.0), tangent=(1.0, 0.0), **LIMITS) > free


@pytest.mark.parametrize('bad', [{'v_max': 0.0}, {'a_lat_max': -1.0}, {'max_accel': math.nan},
                                 {'max_decel': 0.0}, {'v_min': 6.0}, {'v_min': -1.0}])
def test_rejects_limits_that_cannot_drive(bad):
    with pytest.raises(ValueError):
        reference_lap(circle(20.0), start=(20.0, 0.0), tangent=(0.0, 1.0), **dict(LIMITS, **bad))
