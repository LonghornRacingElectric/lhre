"""Check safety:=true wiring: defaults, overrides, types and node startup."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_safety.monitors import SafetyConfig
from lhr_safety.safety_node import SafetyNode
import pytest
import rclpy

# Launch argument -> node parameter, for every threshold the launch exposes.
ARGS = {
    'safety_lidar_timeout_s': 'lidar_timeout_s',
    'safety_odom_timeout_s': 'odom_timeout_s',
    'safety_max_position_std_m': 'max_position_std_m',
    'safety_max_speed_factor': 'max_speed_factor',
    'safety_geofence_radius_m': 'geofence_radius_m',
    'safety_steer_min_speed_mps': 'steer_min_speed_mps',
    'safety_steer_yaw_rate_tol': 'steer_yaw_rate_tol',
    'safety_steer_rel_tol': 'steer_rel_tol',
    'safety_steer_window_s': 'steer_window_s',
}


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
    nodes = [a for a in module._launch_setup(context)
             if isinstance(a, Node) and a.node_package == 'lhr_safety']
    return context, nodes


def _start(parameters):
    """Start SafetyNode on evaluated launch values, as ROS would."""
    ros_args = ['--ros-args']
    for name, value in parameters.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        ros_args.extend(['-p', f'{name}:={text}'])
    rclpy.init(args=ros_args)
    return SafetyNode()


def test_safety_is_off_by_default(monkeypatch):
    _, nodes = _launch(monkeypatch)
    assert nodes == []


@pytest.mark.parametrize('perception, lidar', [('sim', False), ('lidar', True)])
def test_defaults_match_the_node(monkeypatch, perception, lidar):
    context, nodes = _launch(monkeypatch, safety='true', perception=perception)
    (node,) = nodes
    params = evaluate_parameters(context, node._Node__parameters)[0]
    assert params['monitor_lidar'] is lidar
    started = _start(params)
    try:
        config = started._config
        assert config.monitor_lidar is lidar
        defaults = SafetyConfig(wheelbase_m=config.wheelbase_m,
                                max_speed_mps=config.max_speed_mps)
        for param in ARGS.values():
            assert getattr(config, param) == getattr(defaults, param), param
    finally:
        started.destroy_node()
        rclpy.shutdown()


def test_overrides_reach_the_node_as_floats(monkeypatch):
    # Integer-looking input must still arrive as a ROS double.
    overrides = {arg: str(i + 2) for i, arg in enumerate(ARGS)}
    context, nodes = _launch(monkeypatch, safety='true', perception='lidar',
                             **overrides)
    (node,) = nodes
    params = evaluate_parameters(context, node._Node__parameters)[0]
    for arg, param in ARGS.items():
        assert params[param] == float(overrides[arg])
        assert type(params[param]) is float
    started = _start(params)
    try:
        for arg, param in ARGS.items():
            assert getattr(started._config, param) == float(overrides[arg])
    finally:
        started.destroy_node()
        rclpy.shutdown()
