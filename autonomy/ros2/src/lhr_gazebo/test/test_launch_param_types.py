"""Every launch argument reaches its node as the type the node declares."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from lhr_mission_manager.mission_manager_node import MissionManager
from lhr_sensor_sim.inertial_sim_node import InertialSim
from lhr_sensor_sim.sensor_sim_node import SensorSim
from lhr_trackgen.publish_cones import ConePublisher
import pytest
import rclpy
import yaml

# Executable -> (node class, launch arguments it gets under the same name).
NODES = {
    'pursuit_node': (PurePursuit, [
        'lookahead_dist', 'lookahead_min', 'lookahead_curvature_gain',
        'a_lat_max', 'v_min', 'v_max', 'max_accel', 'max_decel']),
    'mission_manager': (MissionManager, ['mission', 'auto_go', 'ready_hold_sec']),
    'inertial_sim': (InertialSim, ['gyro_bias', 'wheel_scale_error']),
    'publish_cones': (ConePublisher, ['seed', 'track_style', 'num_waypoints']),
    'sensor_sim': (SensorSim, ['fov_deg', 'max_range_m']),
}

# A whole number for every double above. Untyped, "5" arrived as an INTEGER
# and the node died declaring the parameter (#130).
WHOLE = {
    'lookahead_dist': '5', 'lookahead_min': '2', 'lookahead_curvature_gain': '3',
    'a_lat_max': '3', 'v_min': '1', 'v_max': '5', 'max_accel': '1',
    'max_decel': '2', 'ready_hold_sec': '0', 'gyro_bias': '0',
    'wheel_scale_error': '1', 'fov_deg': '360', 'max_range_m': '20',
}

# Every branch that adds nodes: both perception modes and estimators, LiDAR
# side classification, safety, a fault, and the cameras.
CONFIGS = [
    {'perception': 'sim', 'estimator': 'truth'},
    {'perception': 'sim', 'estimator': 'ekf', 'safety': 'true',
     'fault': 'cmd_nan'},
    {'perception': 'lidar', 'estimator': 'truth',
     'lidar_classify_sides': 'true'},
    {'perception': 'lidar', 'estimator': 'ekf', 'safety': 'true',
     'fault': 'lidar_drop', 'enable_stereo_camera': 'true',
     'enable_camera_color_classifier': 'true'},
]


def _launch(monkeypatch, **overrides):
    """Evaluate the launch file with its declared defaults plus overrides."""
    pkg = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        'gazebo_param_types', pkg / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: str(pkg))
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(overrides)
    nodes = [a for a in module._launch_setup(context) if isinstance(a, Node)]
    return context, nodes


def _uses_argument(value):
    if isinstance(value, LaunchConfiguration):
        return True
    if isinstance(value, (list, tuple)):
        return any(_uses_argument(v) for v in value)
    return False


def _untyped(node):
    """Return the node's parameters that a launch argument feeds untyped."""
    names = []
    for params in node._Node__parameters:
        if not isinstance(params, dict):
            continue  # a parameter file
        for name, value in params.items():
            if isinstance(value, ParameterValue):
                if getattr(value, 'value_type', None) is not None:
                    continue
                value = value.value
            if _uses_argument(value):
                names.append(name if isinstance(name, str) else ''.join(
                    getattr(s, 'text', '?') for s in name))
    return names


def _evaluate(context, nodes):
    return {n.node_executable: evaluate_parameters(context, n._Node__parameters)[0]
            for n in nodes}


def test_untyped_check_catches_a_bare_argument():
    def node(value):
        return Node(package='p', executable='e', parameters=[{'x': value}])

    assert _untyped(node(LaunchConfiguration('v_max'))) == ['x']
    assert _untyped(node(ParameterValue(
        LaunchConfiguration('v_max'), value_type=float))) == []
    assert _untyped(node(1.0)) == []


@pytest.mark.parametrize('config', CONFIGS)
def test_every_forwarded_argument_is_typed(monkeypatch, config):
    _, nodes = _launch(monkeypatch, **config)
    untyped = {n.node_executable: _untyped(n) for n in nodes}
    assert {exe: names for exe, names in untyped.items() if names} == {}


def test_defaults_are_unchanged(monkeypatch):
    # Untyped, launch_ros YAML-parsed each default; the types must agree.
    context, nodes = _launch(monkeypatch, perception='sim', estimator='ekf')
    params = _evaluate(context, nodes)
    for exe, (_, args) in NODES.items():
        for arg in args:
            expected = yaml.safe_load(context.launch_configurations[arg])
            assert params[exe][arg] == expected, arg
            assert type(params[exe][arg]) is type(expected), arg


@pytest.mark.parametrize('exe', sorted(NODES))
def test_whole_numbers_start_the_node_as_doubles(monkeypatch, exe):
    context, nodes = _launch(monkeypatch, perception='sim', estimator='ekf',
                             **WHOLE)
    params = _evaluate(context, nodes)[exe]
    node_class, args = NODES[exe]
    for arg in args:
        if arg in WHOLE:
            assert params[arg] == float(WHOLE[arg])
            assert type(params[arg]) is float, arg
    # Start the node on the evaluated values, as ROS would.
    ros_args = ['--ros-args']
    for name, value in params.items():
        ros_args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=ros_args)
    node = None
    try:
        node = node_class()
        for arg in args:
            assert node.get_parameter(arg).value == params[arg], arg
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()
