"""Every gate course fits inside the default safety geofence, so no run trips it by design."""

import importlib.util
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from lhr_safety.monitors import SafetyConfig
from lhr_trackgen.rules_autocross import generate_rules_autocross_track
import pytest

PACKAGE = Path(__file__).parents[1]
RADIUS_M = SafetyConfig.geofence_radius_m
# Room for the estimate the fence trips on (the gate allows 0.5 m of EKF
# error), the car's own length past its reference point, and drift at arming.
MARGIN_M = 20.0
# check_envelope caps a rules-compliant lap at 500 m.
MAX_LAP_M = 500.0


def _generate_world():
    spec = importlib.util.spec_from_file_location(
        'fence_world', PACKAGE / 'scripts/generate_world.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reach(spawn, cones):
    return max(math.dist(spawn, cone) for cone in cones)


def _world_reach(path):
    """Farthest cone from the world's spawn pose, the fence's anchor."""
    spawn, cones = None, []
    for item in ET.parse(path).getroot().iter('include'):
        name = item.findtext('name', '')
        xy = [float(v) for v in item.findtext('pose', '0 0 0 0 0 0').split()[:2]]
        if name == 'fsae_vehicle':
            spawn = xy
        elif name.startswith('cone_'):
            cones.append(xy)
    assert spawn is not None and cones, f'{path.name} has no spawn or no cones'
    return _reach(spawn, cones)


def test_any_legal_lap_fits():
    # No point on a closed loop is farther from another than half the lap.
    assert MAX_LAP_M / 2 + MARGIN_M <= RADIUS_M


@pytest.mark.parametrize('world', sorted((PACKAGE / 'worlds').glob('*.sdf')),
                         ids=lambda path: path.name)
def test_committed_world_fits(world):
    assert _world_reach(world) + MARGIN_M <= RADIUS_M


def test_generated_rules_courses_fit():
    # Seeds 1 to 5 are the gate's; the rest sample what the generator makes.
    spawn_for = _generate_world()._compute_spawn
    for seed in range(1, 201):
        left, right = generate_rules_autocross_track(seed=seed)[:2]
        sx, sy, _ = spawn_for(left, right)
        assert _reach((sx, sy), list(left) + list(right)) + MARGIN_M <= RADIUS_M, seed
