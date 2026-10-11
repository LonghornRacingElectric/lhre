"""Verify Gazebo chaining arguments reach track-builder startup in both modes."""

import importlib.util
import math
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_track_builder.track_builder_node import TrackBuilder
import pytest
import rclpy
from rclpy.parameter import Parameter


@pytest.mark.parametrize('perception, classify_sides, pairing_strategy', [
    ('sim', False, 'nearest'), ('lidar', False, 'boundary'),
    ('lidar', True, 'classified'),
])
@pytest.mark.parametrize('algorithm', [
    None, 'constrained_greedy', 'fixed_width_beam_search',
])
def test_chaining_launch_parameters(
    perception, classify_sides, pairing_strategy, algorithm, monkeypatch,
):
    """Check matching defaults, CLI types, forwarding, and initialized state."""
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
    context.launch_configurations['perception'] = perception
    context.launch_configurations['lidar_classify_sides'] = str(classify_sides).lower()
    fields = {
        'chaining_alg': '_chaining_alg',
        'min_step_m': '_min_step_m',
        'max_step_m': '_max_step_m',
        'distance_weight': '_distance_weight',
        'heading_weight': '_heading_weight',
        'beam_width': '_beam_width',
        'beam_depth': '_beam_depth',
    }
    rclpy.init(args=[])
    baseline = TrackBuilder()
    try:
        defaults = {name: baseline.get_parameter(name).value
                    for name in (*fields, 'max_turn_deg')}
    finally:
        baseline.destroy_node()
        rclpy.shutdown()
    expected = defaults.copy()
    if algorithm is not None:
        # Integer-looking float input must still become a ROS double.
        overrides = {
            'chaining_alg': algorithm, 'min_step_m': '0.6',
            'max_step_m': '6', 'max_turn_deg': '45',
            'distance_weight': '2', 'heading_weight': '3',
            'beam_width': '5', 'beam_depth': '20',
        }
        context.launch_configurations.update(overrides)
        expected = {name: type(defaults[name])(value)
                    for name, value in overrides.items()}
    actions = module._launch_setup(context)
    builders = [action for action in actions
                if isinstance(action, Node)
                and action.node_package == 'lhr_track_builder']
    assert len(builders) == 1
    parameters = evaluate_parameters(context, builders[0]._Node__parameters)[0]
    for name, value in expected.items():
        assert parameters[name] == value
        assert type(parameters[name]) is type(value)
    assert parameters['pairing_strategy'] == pairing_strategy
    # Feed the evaluated launch values through ROS startup, including validation.
    ros_args = ['--ros-args']
    for name, value in parameters.items():
        text_value = str(value).lower() if isinstance(value, bool) else str(value)
        ros_args.extend(['-p', f'{name}:={text_value}'])
    rclpy.init(args=ros_args)
    builder = TrackBuilder()
    try:
        assert builder._pairing_algorithm.__name__ == '_pair_' + pairing_strategy
        for name, field in fields.items():
            assert getattr(builder, field) == expected[name]
        assert builder._max_turn_rad == math.radians(expected['max_turn_deg'])
        assert builder.get_parameter('max_step_m').type_ == Parameter.Type.DOUBLE
        assert builder.get_parameter('beam_width').type_ == Parameter.Type.INTEGER
        assert builder._get_chaining_algorithm().__name__ == {
            'greedy': '_greedy_chain_path',
            'constrained_greedy': '_constrained_greedy',
            'fixed_width_beam_search': '_fixed_width_beam_search',
        }[expected['chaining_alg']]
    finally:
        builder.destroy_node()
        rclpy.shutdown()
