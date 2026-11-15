"""Nothing arms or moves until the stack is up: READY waits for safety, pursuit for the mission."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
import pytest


def _params(monkeypatch, exe='mission_manager', **overrides):
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_prearm', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    assert set(overrides) <= set(context.launch_configurations)
    context.launch_configurations.update(overrides)
    (node,) = [a for a in module._launch_setup(context)
               if isinstance(a, Node) and a.node_executable == exe]
    return evaluate_parameters(context, node._Node__parameters)[0]


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_safety_on_waits_for_the_heartbeat(monkeypatch, perception):
    params = _params(monkeypatch, safety='true', perception=perception)
    assert params['require_safety_heartbeat'] is True
    assert params['safety_settle_s'] == 2.0


def test_safety_off_does_not_wait(monkeypatch):
    params = _params(monkeypatch, safety='false')
    assert params['require_safety_heartbeat'] is False


def test_settle_time_override_stays_a_double(monkeypatch):
    params = _params(monkeypatch, safety='true', safety_settle_s='3')
    assert params['safety_settle_s'] == 3.0
    assert type(params['safety_settle_s']) is float


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('safety', ['true', 'false'])
def test_pursuit_waits_for_the_mission_manager(monkeypatch, perception, safety):
    params = _params(monkeypatch, 'pursuit_node', perception=perception, safety=safety)
    assert params['wait_for_mission'] is True
