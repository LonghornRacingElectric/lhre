"""MVS scoring parameters must reach startup without using planner geometry."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_metrics.metrics_node import MetricsNode, SCORING_PARAMS
import pytest
import rclpy


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('override', [False, True])
def test_metrics_scoring_parameters(perception, override):
    path = Path(__file__).parents[1] / 'launch/mvs_demo.launch.py'
    spec = importlib.util.spec_from_file_location('mvs_scoring_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    context.launch_configurations['perception'] = perception
    description = module.generate_launch_description()
    for action in description.entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    expected = dict(SCORING_PARAMS)
    if override:
        values = {'reference_closed': 'false', 'min_progress_fraction': '1',
                  'lap_start_speed': '1', 'max_odom_gap_sec': '2',
                  'off_track_threshold': '3', 'start_radius': '4', 'min_lap_time': '6'}
        context.launch_configurations.update(values)
        expected.update({name: (value == 'true' if name == 'reference_closed' else float(value))
                         for name, value in values.items()})
    node = next(action for action in description.entities
                if isinstance(action, Node) and action.node_executable == 'metrics_node')
    params = evaluate_parameters(context, node._Node__parameters)[0]
    for name, value in expected.items():
        assert params[name] == value
        assert type(params[name]) is type(value)
    args = ['--ros-args']
    for name, value in params.items():
        args += ['-p', f'{name}:={json.dumps(value)}']
    rclpy.init(args=args)
    metrics = None
    try:
        metrics = MetricsNode()
        assert metrics._reference is None
        assert metrics._reference_source == ''
        for name, value in expected.items():
            assert metrics.get_parameter(name).value == value
        topics = [name for name, _ in metrics.get_topic_names_and_types()]
        assert '/lhr/track/cones' in topics
    finally:
        if metrics is not None:
            metrics.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('settings', [
    {'mission': 'acceleration'}, {'track_style': 'acceleration'},
])
def test_acceleration_reference_defaults_open(settings):
    path = Path(__file__).parents[1] / 'launch/mvs_demo.launch.py'
    spec = importlib.util.spec_from_file_location('mvs_open_reference', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    context.launch_configurations.update(settings)
    description = module.generate_launch_description()
    for action in description.entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    node = next(action for action in description.entities
                if isinstance(action, Node) and action.node_executable == 'metrics_node')
    params = evaluate_parameters(context, node._Node__parameters)[0]
    assert params['reference_closed'] is False
