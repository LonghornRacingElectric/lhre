"""Exercise the rules envelope, including invalid geometry and reproducibility."""

import math

from lhr_trackgen.rules_autocross import (
    BASE, check_envelope, generate_rules_autocross_track)
import pytest


def capsule(straight=75., radius=10., width=3.5):
    """Build an analytically known circuit, independent of the generator."""
    left, right = [], []
    points = []
    for i in range(100):
        t = i/100
        points.append((straight*t, 0., 0.))
    for i in range(100):
        t = math.pi*i/100
        points.append((straight+radius*math.sin(t), radius*(1-math.cos(t)), t))
    for i in range(100):
        t = i/100
        points.append((straight*(1-t), 2*radius, math.pi))
    for i in range(100):
        t = math.pi*i/100
        points.append((-radius*math.sin(t), radius*(1+math.cos(t)), math.pi+t))
    for x, y, heading in points:
        h = (width+BASE)/2
        left.append((x-h*math.sin(heading), y+h*math.cos(heading)))
        right.append((x+h*math.sin(heading), y-h*math.cos(heading)))
    return left, right


@pytest.mark.parametrize('seed', range(100))
def test_seeded_courses_hold_rules_envelope(seed):
    width = 3.+(seed % 4)*.5
    left, right = generate_rules_autocross_track(seed, width_m=width)
    report = check_envelope(left, right)
    assert 200 <= report['lap_length_m'] <= 500
    assert report['longest_straight_m'] <= 80
    assert report['minimum_radius_m'] >= 4.5
    assert report['minimum_clear_width_m'] >= width
    assert len(left) == len(right)
    for side in (left, right):
        assert max(math.dist(a, b) for a, b in zip(side, side[1:]+side[:1])) <= 2


def test_reproducibility_and_seed_diversity():
    a = generate_rules_autocross_track(5)
    assert a == generate_rules_autocross_track(5)
    assert a != generate_rules_autocross_track(6)
    # Legacy radial-waypoint flags must not silently change this style.
    assert a == generate_rules_autocross_track(5, radius_m=99., jitter_m=0., num_waypoints=20)


def test_known_legal_capsule_measurements():
    report = check_envelope(*capsule())
    assert report['lap_length_m'] == pytest.approx(150+20*math.pi, abs=.01)
    assert report['longest_straight_m'] == pytest.approx(75, abs=1.)
    assert report['minimum_radius_m'] == pytest.approx(10., abs=.01)
    assert report['minimum_clear_width_m'] == pytest.approx(3.5, abs=.002)


@pytest.mark.parametrize('kwargs, reason', [
    ({'straight': 20.}, 'lap length'),
    ({'straight': 75., 'radius': 70.}, 'lap length'),
    ({'straight': 81.}, 'straight'),
    ({'width': 2.9}, 'clear width'),
])
def test_out_of_envelope_layouts_are_rejected(kwargs, reason):
    with pytest.raises(ValueError, match=reason):
        check_envelope(*capsule(**kwargs))


def test_tight_turn_is_rejected():
    left, right = [], []
    # An ellipse with minimum radius b^2/a = 4 m, and a legal lap length.
    for i in range(600):
        t = 2*math.pi*i/600
        x, y = 55*math.cos(t), math.sqrt(220)*math.sin(t)
        nx, ny = -math.cos(t)/55, -math.sin(t)/math.sqrt(220)
        scale = (3.5+BASE)/2/math.hypot(nx, ny)
        left.append((x+scale*nx, y+scale*ny))
        right.append((x-scale*nx, y-scale*ny))
    with pytest.raises(ValueError, match='turn radius'):
        check_envelope(left, right)


def test_crossed_boundary_and_crowded_return_are_rejected():
    left, right = capsule()
    left[50], right[50] = right[50], left[50]
    with pytest.raises(ValueError):
        check_envelope(left, right)
    # A nonlocal boundary folds into a neighboring section.
    left, right = capsule()
    left[250] = left[50]
    with pytest.raises(ValueError):
        check_envelope(left, right)


@pytest.mark.parametrize('kwargs', [
    {'width_m': 2.99}, {'width_m': math.inf}, {'width_m': math.nan},
    {'cone_spacing_m': 0}, {'cone_spacing_m': math.inf}, {'cone_spacing_m': -1},
])
def test_bad_generator_arguments_fail(kwargs):
    with pytest.raises(ValueError):
        generate_rules_autocross_track(**kwargs)


def test_rejected_candidates_are_regenerated(monkeypatch):
    import lhr_trackgen.rules_autocross as module
    actual = module.check_envelope
    calls = []

    def check(left, right):
        calls.append(left)
        if len(calls) == 1:
            raise ValueError('injected invalid candidate')
        return actual(left, right)

    monkeypatch.setattr(module, 'check_envelope', check)
    module.generate_rules_autocross_track(1)
    assert len(calls) >= 2
    assert calls[0] != calls[1]


def test_exhausted_retries_fail_closed(monkeypatch):
    import lhr_trackgen.rules_autocross as module

    def reject(*_):
        raise ValueError('test rejection')

    monkeypatch.setattr(module, 'check_envelope', reject)
    with pytest.raises(ValueError, match='100 attempts: test rejection'):
        module.generate_rules_autocross_track(1)


@pytest.mark.parametrize('seed, expected', [
    (1, '61f91afcde0d4ba6ce5ca62db76f2b86734cf9ac37825b4d7e5cf4379141701d'),
    (2, 'dae37f9efa5669efe9ed4d23b3b01b902cefc29e70430d9ea1f81dce9bc4a843'),
    (3, '3698db9ec7e5cf4a5e2541ef4bee206243a8cc0d9f21a23e77461010ae40697b'),
    (4, '52c1660f56861e6b7bc838f16c543431a2b7ed55d8a5b4f3b8d99c0f376535a1'),
    (5, '7cb17cc33127ced9af5f2dcbc799c2638008af5119157339bb78b11b89e5c488'),
])
def test_mvp_autocross_seeds_are_unchanged(seed, expected):
    import hashlib
    import json

    from lhr_trackgen.publish_cones import generate_autocross_track

    cones = generate_autocross_track(seed=seed)
    # libm differs in last-bit rounding between macOS and Linux.
    rounded = [[[round(v, 6) for v in p] for p in side] for side in cones]
    encoded = json.dumps(rounded, separators=(',', ':')).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


def test_crossed_return_lanes_fail_despite_legal_length_and_radius():
    left, right = [], []
    # Gerono's figure-eight has legal lap length and curvature at this scale.
    for i in range(300):
        t = 2*math.pi*i/300
        x, y = 50*math.sin(t), 20*math.sin(2*t)
        dx, dy = 50*math.cos(t), 40*math.cos(2*t)
        h = 2/math.hypot(dx, dy)
        left.append((x-dy*h, y+dx*h))
        right.append((x+dy*h, y-dx*h))
    with pytest.raises(ValueError, match='crowd|cross'):
        check_envelope(left, right)


def test_long_straight_spanning_index_seam_is_rejected():
    left, right = capsule(straight=81.)
    left, right = left[50:]+left[:50], right[50:]+right[:50]
    with pytest.raises(ValueError, match='straight'):
        check_envelope(left, right)
