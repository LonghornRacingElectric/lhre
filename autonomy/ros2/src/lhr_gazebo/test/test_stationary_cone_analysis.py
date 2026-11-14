"""Check stationary diagnostic scoring against geometric and temporal counterexamples."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest


def analysis():
    scripts = Path(__file__).parents[1] / 'scripts'
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(
            'stationary_cone_analysis', scripts / 'analyze_stationary_cones.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_geometry_counts_duplicates_separately_from_displaced_outliers():
    module = analysis()
    result = module.geometry([[0, 0], [.1, 0], [4, 0]], [[0, 0], [2, 0]])
    assert result['count'] == 3
    assert result['duplicates'] == 1
    assert result['unmatched'] == 1
    assert result['p95_m'] > 1


def test_temporal_jitter_does_not_confuse_fixed_sensor_offset_with_motion():
    module = analysis()
    stable = module.jitter({0: [[100., -30.]]*20, 1: [[0., 0.]]*2})
    moving = module.jitter({0: [[float(k), 0.] for k in range(20)]})
    assert stable['landmarks_with_10_samples'] == 1
    assert stable['radial_scatter_m']['max'] == 0
    assert moving['radial_scatter_m']['p95'] > 9


def test_empty_geometry_is_unknown_rather_than_perfect_accuracy():
    module = analysis()
    assert module.geometry([], [[0, 0]])['p95_m'] is None
    assert module.distribution([float('nan'), float('inf')]) is None


def test_sensor_relative_geometry_is_invariant_to_a_shared_rigid_transform():
    module = analysis()
    pose = np.array([2., 3., 0., 0., 0., np.sin(.4), np.cos(.4)])
    points = np.array([[4., 0.], [8., 1.]])
    mapped = module.to_world(points, pose)
    result = module.geometry(module.local(mapped, pose), points)
    assert result['p95_m'] == pytest.approx(0, abs=1e-12)
    assert result['unmatched'] == 0


@pytest.mark.parametrize('movement,command,state,expected', [
    (0., 0., 'READY', True), (.03, 0., 'READY', False),
    (0., .1, 'READY', False), (0., 0., 'DRIVING', False)])
def test_stationarity_requires_physics_commands_and_mission_agreement(
        tmp_path, monkeypatch, movement, command, state, expected):
    module = analysis()
    rows = np.zeros((2, 14))
    rows[:, 0], rows[:, 7], rows[1, 1] = [2., 20.], 1., movement
    np.savetxt(tmp_path / 'probe-odom3d.csv', rows, delimiter=',', header='reference', comments='')
    msg = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=2, nanosec=0)),
                          drive=SimpleNamespace(speed=command))
    topics = ['/lhr/vehicle/cmd', '/lhr/mission/status']
    entries = iter([(topics[0], msg, 0), (topics[1], SimpleNamespace(data=state), 0)])
    remaining = iter([True, True, False])
    reader = SimpleNamespace(
        open=lambda *_: None, has_next=lambda: next(remaining), read_next=lambda: next(entries),
        get_all_topics_and_types=lambda: [SimpleNamespace(name=t, type='unused') for t in topics])
    monkeypatch.setattr(module, 'SequentialReader', lambda: reader)
    monkeypatch.setattr(module, 'get_message', lambda _: None)
    monkeypatch.setattr(module, 'deserialize_message', lambda raw, _: raw)
    _, result = module.audit_motion(tmp_path, 2., 20.)
    assert result['stationary_verified'] is expected
