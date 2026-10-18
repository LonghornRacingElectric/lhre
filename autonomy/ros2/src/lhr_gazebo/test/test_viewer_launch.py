"""Check the Foxglove, recording and viewer-support launch wiring."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.utilities import (
    normalize_to_list_of_substitutions,
    perform_substitutions,
)
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_vehicle import load_vehicle
from lhr_vehicle.vehicle_viz_node import VehicleVizNode
import pytest
import rclpy


def _launch(monkeypatch, **overrides):
    """Evaluate the launch file with its declared defaults plus overrides."""
    launch_path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_demo', launch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(overrides)
    return context, module._launch_setup(context)


def _nodes(actions, package):
    return [a for a in actions
            if isinstance(a, Node) and a.node_package == package]


def _text(context, value):
    """Resolve a launch argument whether it was given as text or substitutions."""
    return perform_substitutions(
        context, normalize_to_list_of_substitutions(value))


def _params(context, node):
    return evaluate_parameters(context, node._Node__parameters)[0]


def _recorders(context, actions):
    """Return the command line of every ros2 bag process."""
    lines = []
    for action in actions:
        # A launch_ros Node is an ExecuteProcess too, and resolving its
        # command looks up the package, which CI may not have installed.
        if isinstance(action, ExecuteProcess) and not isinstance(action, Node):
            line = [_text(context, part) for part in action.cmd]
            if line[:3] == ['ros2', 'bag', 'record']:
                lines.append(line)
    return lines


def test_defaults_start_no_bridge_and_no_recorder(monkeypatch):
    context, actions = _launch(monkeypatch)
    assert _nodes(actions, 'foxglove_bridge') == []
    assert _recorders(context, actions) == []


@pytest.mark.parametrize('port', [None, '9000'])
def test_foxglove_bridge_port_is_an_integer(monkeypatch, port):
    overrides = {'foxglove': 'true'}
    if port is not None:
        overrides['foxglove_port'] = port
    context, actions = _launch(monkeypatch, **overrides)
    bridges = _nodes(actions, 'foxglove_bridge')
    assert len(bridges) == 1
    value = _params(context, bridges[0])['port']
    assert value == int(port or 8765)
    assert type(value) is int


def test_record_shares_run_id_with_metrics(monkeypatch):
    context, actions = _launch(
        monkeypatch, record='true', run_id='trial_001', bag_dir='bags')
    (line,) = _recorders(context, actions)
    assert line[line.index('--output') + 1] == 'bags/trial_001'
    assert 'run_id=trial_001' in line
    assert line[line.index('--storage') + 1] == 'mcap'
    (metrics,) = _nodes(actions, 'lhr_metrics')
    assert _params(context, metrics)['run_id'] == 'trial_001'


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_truth_cones_and_viewer_nodes_in_both_modes(monkeypatch, perception):
    context, actions = _launch(monkeypatch, perception=perception)
    assert len(_nodes(actions, 'lhr_trackgen')) == 1
    (tf,) = _nodes(actions, 'tf2_ros')
    args = [_text(context, a) for a in tf._Node__arguments]
    x, y, z = load_vehicle().lidar_position_m
    assert args == ['--x', str(x), '--y', str(y), '--z', str(z),
                    '--frame-id', 'base_link', '--child-frame-id', 'lidar']
    assert len(_nodes(actions, 'lhr_vehicle')) == 1


@pytest.mark.parametrize('setting, expected', [(None, False), ('true', True)])
def test_vehicle_mesh_reaches_the_node(monkeypatch, setting, expected):
    overrides = {} if setting is None else {'vehicle_mesh': setting}
    context, actions = _launch(monkeypatch, **overrides)
    (viz,) = _nodes(actions, 'lhr_vehicle')
    value = _params(context, viz)['use_mesh']
    assert value is expected
    # Start the node on the evaluated value, as ROS would.
    rclpy.init(args=['--ros-args', '-p', f'use_mesh:={str(value).lower()}'])
    node = VehicleVizNode()
    try:
        assert node.get_parameter('use_mesh').value is expected
    finally:
        node.destroy_node()
        rclpy.shutdown()
