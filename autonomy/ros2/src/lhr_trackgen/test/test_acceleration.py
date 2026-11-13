"""The acceleration course matches the rules layout the stack can drive."""

from lhr_trackgen.cone_geometry import CONE_SPECS
from lhr_trackgen.publish_cones import (
    ACCEL_EXIT_M, ACCEL_LENGTH_M, ACCEL_WIDTH_M, cone_kinds,
    generate_acceleration_track, GENERATORS, pairing_width_m)
import pytest


@pytest.fixture
def course():
    left, right = generate_acceleration_track()
    lk, rk = cone_kinds('acceleration', left, right)
    return left, right, lk, rk


def _lines(cones, kinds):
    """Midpoints of consecutive large-orange pairs: the timing lines."""
    xs = [x for (x, _), k in zip(cones, kinds) if k == 'orange_large']
    return [(a + b) / 2.0 for a, b in zip(xs[::2], xs[1::2])]


def test_start_and_finish_lines_are_75_m_apart(course):
    left, right, lk, rk = course
    assert _lines(left, lk) == pytest.approx([0.0, ACCEL_LENGTH_M])
    assert _lines(right, rk) == pytest.approx([0.0, ACCEL_LENGTH_M])


def test_clear_width_between_cone_bases_is_the_rules_width(course):
    left, right, lk, rk = course
    assert len(left) == len(right)
    for (lx, ly), (rx, ry), kl, kr in zip(left, right, lk, rk):
        assert lx == rx
        clear = ((ly - CONE_SPECS[kl].base_width_m / 2.0)
                 - (ry + CONE_SPECS[kr].base_width_m / 2.0))
        assert clear == pytest.approx(ACCEL_WIDTH_M)


def test_pairing_width_is_the_small_cone_center_spacing(course):
    left, right, lk, _ = course
    small = [ly - ry for (_, ly), (_, ry), k in zip(left, right, lk)
             if k != 'orange_large']
    assert small == pytest.approx([pairing_width_m('acceleration')] * len(small))
    # The large gate cones sit a little wider, still inside track_builder's
    # default 1 m tolerance.
    large = [ly - ry for (_, ly), (_, ry), k in zip(left, right, lk)
             if k == 'orange_large']
    assert all(abs(w - pairing_width_m('acceleration')) < 0.1 for w in large)
    assert pairing_width_m('autocross', 3.5) == 3.5


def test_colors(course):
    left, right, lk, rk = course
    for side, own, kinds in ((left, 'blue', lk), (right, 'yellow', rk)):
        for (x, _), kind in zip(side, kinds):
            if kind == 'orange_large':
                continue
            assert kind == (own if x < ACCEL_LENGTH_M else 'orange_small')


def test_exit_lane_covers_the_stopping_distance(course):
    left, _, _, _ = course
    assert left[-1][0] == pytest.approx(ACCEL_LENGTH_M + ACCEL_EXIT_M)


@pytest.mark.parametrize('spacing', [2.0, 3.0])
def test_gaps_fit_the_perception_stack(spacing):
    # Wider than lidar_cone_detector's 1.5 m dedup_radius, no wider than
    # the requested spacing (and so track_builder's 4 m max_step_m).
    left, _ = generate_acceleration_track(cone_spacing_m=spacing)
    gaps = [b[0] - a[0] for a, b in zip(left, left[1:])]
    assert min(gaps) > 1.5
    assert max(gaps) <= max(spacing, 2.0) + 1e-9


@pytest.mark.parametrize('spacing', [0.0, -2.0, float('inf'), float('nan')])
def test_bad_spacing_is_rejected(spacing):
    # Silently building a sparse course would look like a perception fault.
    with pytest.raises(ValueError, match='cone_spacing_m'):
        generate_acceleration_track(cone_spacing_m=spacing)


def test_layout_ignores_seed_and_width():
    assert (generate_acceleration_track(seed=1, width_m=3.5)
            == generate_acceleration_track(seed=9, width_m=8.0))
    assert GENERATORS['acceleration'] is generate_acceleration_track


def test_other_styles_stay_blue_and_yellow():
    left, right = GENERATORS['oval']()
    lk, rk = cone_kinds('oval', left, right)
    assert set(lk) == {'blue'} and set(rk) == {'yellow'}
