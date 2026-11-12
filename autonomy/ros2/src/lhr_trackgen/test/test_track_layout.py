"""Check generated cone layouts stay closed, paired, evenly spaced and uncrossed."""

import math

from lhr_trackgen.publish_cones import (
    _boundaries_cross, _minimum_radius, generate_autocross_track,
    generate_oval_track)
import pytest


def _gaps(cones):
    return [math.dist(cones[i - 1], cones[i]) for i in range(len(cones))]


@pytest.mark.parametrize('generate', [generate_autocross_track, generate_oval_track])
@pytest.mark.parametrize('seed', [0, 1, 7, 42, 123])
def test_layout_spacing_width_and_closure(generate, seed):
    left, right = generate(seed=seed, width_m=3.5, cone_spacing_m=2.)
    assert len(left) == len(right)
    # Index -1 to 0 is the closing gap, which older versions left open.
    for side in (left, right):
        assert max(_gaps(side)) <= 2.
    assert all(math.dist(a, b) == pytest.approx(3.5) for a, b in zip(left, right))
    assert min(math.dist(a, b) for a in left for b in right) > 3.5 - 1e-6
    assert not _boundaries_cross(left, right)


def test_autocross_bends_stay_drivable():
    left, right = generate_autocross_track(seed=1, jitter_m=20.)
    centers = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(left, right)]
    assert _minimum_radius(centers) >= 6. * .9  # cone sampling coarsens the estimate


def test_oval_cones_follow_the_curve():
    left, right = generate_oval_track(radius_m=25., aspect=.6)
    for a, b in zip(left, right):
        # Each pair sits across the ellipse, never diagonally through a bend.
        x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        normal = (x / 25. ** 2, y / 15. ** 2)
        across = (a[0] - b[0], a[1] - b[1])
        cosine = abs(normal[0] * across[1] - normal[1] * across[0]) / (
            math.hypot(*normal) * math.hypot(*across))
        assert cosine < .05


@pytest.mark.parametrize('kwargs', [
    {'width_m': 0.}, {'cone_spacing_m': -1.}, {'radius_m': math.nan}])
def test_invalid_dimensions_are_rejected(kwargs):
    with pytest.raises(ValueError):
        generate_autocross_track(**kwargs)


def test_impossible_oval_is_rejected():
    with pytest.raises(ValueError):
        generate_oval_track(radius_m=5., aspect=.2, width_m=3.5)
