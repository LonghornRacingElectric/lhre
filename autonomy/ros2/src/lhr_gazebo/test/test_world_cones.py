"""Every committed world holds exactly the cones trackgen publishes for it."""

import importlib.util
from pathlib import Path
import re

from lhr_trackgen.publish_cones import cone_kinds, GENERATORS
import pytest

PKG = Path(__file__).parents[1]
WORLDS = sorted((PKG / 'worlds').glob('*_seed*.sdf'))
CONE = re.compile(
    r'<uri>model://(cone_\w+)</uri>\s*<name>[^<]*</name>\s*'
    r'<pose>(\S+) (\S+) ')


def _cone_models():
    path = PKG / 'scripts/generate_world.py'
    spec = importlib.util.spec_from_file_location('generate_world', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CONE_MODELS


def test_there_are_worlds_to_check():
    assert {p.stem for p in WORLDS} >= {
        'autocross_seed1', 'autocross_seed5', 'oval_seed1', 'acceleration_seed1'}


@pytest.mark.parametrize('world', WORLDS, ids=lambda p: p.stem)
def test_world_matches_the_published_cones(world):
    # The physical cones and publish_cones' truth must be the same track: a
    # stale world (a generator change, a hand edit) splits perception from
    # the reference it is scored against.
    style, seed = world.stem.rsplit('_seed', 1)
    left, right = GENERATORS[style](
        seed=int(seed), num_waypoints=10, radius_m=25.0, jitter_m=10.0,
        width_m=3.5, cone_spacing_m=2.0)
    left_kinds, right_kinds = cone_kinds(style, left, right)
    models = _cone_models()

    def key(cone):
        # The SDF keeps 4 decimals; symmetric tracks have near-ties in x.
        return cone[0], round(cone[1], 2), round(cone[2], 2)

    want = sorted(((models[kind], x, y) for (x, y), kind in zip(
        left + right, left_kinds + right_kinds)), key=key)
    got = sorted(((model, float(x), float(y))
                  for model, x, y in CONE.findall(world.read_text())), key=key)
    assert len(got) == len(want)
    for (gm, gx, gy), (wm, wx, wy) in zip(got, want):
        assert gm == wm
        assert gx == pytest.approx(wx, abs=1e-3)
        assert gy == pytest.approx(wy, abs=1e-3)
