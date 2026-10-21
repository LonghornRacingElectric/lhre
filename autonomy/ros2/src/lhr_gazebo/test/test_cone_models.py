"""Keep committed Gazebo cone assets consistent with the shared profile."""

import importlib.util
from pathlib import Path

from lhr_trackgen.cone_geometry import CONE_SPECS
import pytest


@pytest.mark.parametrize('kind', list(CONE_SPECS))
def test_cone_assets_match_generator_with_collisions(kind):
    package = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'generate_cones', package / 'scripts' / 'generate_cone_models.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    for name, content in generator.render(kind).items():
        assert (package / 'models' / ('cone_' + kind) / name).read_text() == content
    assert '<collision name="collision">' in generator.render(kind)['model.sdf']
