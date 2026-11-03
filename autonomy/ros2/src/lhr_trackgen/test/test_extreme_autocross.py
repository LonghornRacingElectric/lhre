"""Check fixed courses hit their named limits without leaving the rules envelope."""

import math

from lhr_trackgen.extreme_autocross import (
    _turn, extreme_pairing_width, generate_extreme_track, SCENARIOS)
from lhr_trackgen.rules_autocross import BASE, check_envelope
import pytest


@pytest.mark.parametrize('name', SCENARIOS)
def test_fixed_course_envelope_spacing_and_identity(name):
    left, right = generate_extreme_track(name)
    report = check_envelope(left, right)
    assert 200 <= report['lap_length_m'] <= 500
    assert report['longest_straight_m'] <= 80+1e-6
    assert report['minimum_radius_m'] >= 4.5-1e-6
    assert report['minimum_clear_width_m'] >= 3
    assert (left, right) == generate_extreme_track(name)
    # Generic seeded-generator dimensions cannot accidentally alter fixtures.
    assert (left, right) == generate_extreme_track(name, width_m=10., cone_spacing_m=5.)
    assert extreme_pairing_width(name) == pytest.approx(math.dist(left[0], right[0]))
    for side in (left, right):
        assert max(math.dist(a, b) for a, b in zip(side, side[1:]+side[:1])) <= 2.
    left[0] = (999., 999.)
    assert generate_extreme_track(name)[0][0] != left[0]


@pytest.mark.parametrize('name', ['extreme_narrow', 'extreme_combined'])
def test_clear_width_is_three_metres_throughout_within_one_centimetre(name):
    left, right = generate_extreme_track(name)
    assert check_envelope(left, right)['minimum_clear_width_m'] >= 3.
    assert max(math.dist(a, b)-BASE for a, b in zip(left, right)) <= 3.01


@pytest.mark.parametrize('name', ['extreme_hairpin', 'extreme_combined'])
def test_eighty_metre_approach_and_nine_metre_turning_diameter(name):
    left, right = generate_extreme_track(name)
    report = check_envelope(left, right)
    assert report['longest_straight_m'] == pytest.approx(80., abs=1e-8)
    assert 2*report['minimum_radius_m'] == pytest.approx(9., abs=1e-8)
    center = [((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(left, right)]
    # The right-hand half-turn follows the approach directly: find its start
    # at the end of a horizontal +x run and measure that run independently.
    run = 0.
    found = False
    for i, (a, b) in enumerate(zip(center, center[1:]+center[:1])):
        if abs(a[1]-b[1]) < 1e-8 and b[0] > a[0]:
            run += math.dist(a, b)
        else:
            if run == pytest.approx(80., abs=1e-8):
                c = center[(i+2) % len(center)]
                cross = abs((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
                radius = math.dist(a, b)*math.dist(b, c)*math.dist(a, c)/(2*cross)
                assert radius == pytest.approx(4.5, abs=1e-7)
                found = True
            run = 0.
    assert found


def test_long_lap_reaches_five_hundred_metres():
    report = check_envelope(*generate_extreme_track('extreme_500m'))
    assert 499.9999 < report['lap_length_m'] <= 500


def test_slalom_is_close_to_tightest_legal_curvature():
    report = check_envelope(*generate_extreme_track('extreme_slalom_chicane'))
    assert 4.5 <= report['minimum_radius_m'] < 5.


def test_turn_tightens_toward_apex_and_reverses_heading():
    points = _turn(4.5, decreasing=True)
    radii = []
    for a, b in zip(points, points[1:]):
        chord = math.dist(a[:2], b[:2])
        radii.append(chord/(2*math.sin((b[2]-a[2])/2)))
    first_half = radii[:len(radii)//2]
    assert all(a > b for a, b in zip(first_half, first_half[1:]))
    assert first_half[0] > 8.
    assert 4.5 <= first_half[-1] < 4.7
    assert points[-1][0] == pytest.approx(0., abs=1e-10)
    assert points[-1][2] == pytest.approx(math.pi)


def test_adjacent_return_lanes_are_in_the_same_lidar_neighborhood():
    left, right = generate_extreme_track('extreme_doubleback')
    center = [((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(left, right)]
    # The two long parallel lanes are nine metres apart. Compare matching
    # straight samples with opposite route directions, away from the turns.
    ys = [p[1] for p in center if 25 < abs(p[0]) < 30]
    assert max(ys)-min(ys) == pytest.approx(9., abs=.01)


@pytest.mark.parametrize('name', SCENARIOS)
def test_named_courses_reject_other_seed_identities(name):
    with pytest.raises(ValueError, match='seed 1'):
        generate_extreme_track(name, seed=2)


def test_unknown_name_fails():
    with pytest.raises(ValueError, match='unknown extreme'):
        generate_extreme_track('extreme_typo')
