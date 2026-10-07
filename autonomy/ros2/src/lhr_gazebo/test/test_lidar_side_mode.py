"""Check that the LiDAR launch selects matching cone and path modes."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


class _Node:
    """Record launch-node configuration without starting ROS processes."""

    def __init__(self, **kwargs):
        self.name = kwargs['name']
        self.parameters = kwargs['parameters'][0]


@pytest.mark.parametrize('setting, enabled, strategy', [
    ('true', True, 'classified'),
    ('false', False, 'boundary'),
])
def test_lidar_side_mode_configures_detector_and_builder(
        monkeypatch, setting, enabled, strategy):
    """Unclassified markers must be consumed by the boundary strategy."""
    launch_path = (Path(__file__).resolve().parents[1]
                   / 'launch' / 'gazebo_demo.launch.py')
    spec = importlib.util.spec_from_file_location('gazebo_demo', launch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'Node', _Node)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/world.sdf')
    monkeypatch.setattr(
        module, 'get_package_share_directory', lambda _: '/tmp')

    context = SimpleNamespace(launch_configurations={
        'perception': 'lidar',
        'lidar_classify_sides': setting,
        'estimator': 'truth',
        'gui': 'false',
        'rviz': 'false',
        'enable_metrics': 'false',
    })
    nodes = {
        action.name: action for action in module._launch_setup(context)
        if isinstance(action, _Node)
    }

    assert nodes['lidar_cone_detector'].parameters['classify_sides'] is enabled
    assert nodes['track_builder'].parameters['pairing_strategy'] == strategy
