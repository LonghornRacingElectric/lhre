"""Metrics must score the selected Gazebo world with typed scoring overrides."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_metrics.metrics_node import MetricsNode
from lhr_metrics.reference import ReferenceTrack, world_centerline
import pytest
import rclpy
from rclpy.parameter import Parameter


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('override', [False, True])
def test_reference_source_and_scoring_overrides(perception, estimator, override, monkeypatch):
    package = Path(__file__).parents[1]
    world = package / 'worlds/autocross_seed1.sdf'
    alternate = package / 'worlds/oval_seed1.sdf'
    spec = importlib.util.spec_from_file_location(
        'gazebo_reference_launch', package / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: str(world))
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(perception=perception, estimator=estimator)
    expected = {'reference_closed': True, 'min_progress_fraction': .98, 'lap_start_speed': .1,
                'max_odom_gap_sec': 1., 'off_track_threshold': 2., 'start_radius': 2.,
                'min_lap_time': 5.}
    if override:
        values = {'reference_world': str(alternate), 'reference_closed': 'false',
                  'min_progress_fraction': '1', 'lap_start_speed': '1',
                  'max_odom_gap_sec': '2', 'off_track_threshold': '3',
                  'start_radius': '4', 'min_lap_time': '6'}
        context.launch_configurations.update(values)
        expected = {name: (value == 'true' if name == 'reference_closed' else float(value))
                    for name, value in values.items() if name != 'reference_world'}
    node = next(action for action in module._launch_setup(context)
                if isinstance(action, Node) and action.node_executable == 'metrics_node')
    params = evaluate_parameters(context, node._Node__parameters)[0]
    selected = alternate if override else world
    assert params['reference_world'] == str(selected)
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
        reference = ReferenceTrack(world_centerline(selected), closed=not override)
        assert metrics._reference.sha256 == reference.sha256
        assert metrics._reference_source == str(selected)
        assert metrics._run_params['mission'] == 'autocross'
        assert metrics._run_params['seed'] == '1'
        assert metrics._run_params['perception'] == perception
        assert metrics._run_params['estimator'] == estimator
        assert metrics._run_params['plant'] == 'gazebo'
        assert metrics._truth_topic == ('/lhr/vehicle/odom_truth' if estimator == 'ekf' else '')
        for name, value in expected.items():
            parameter = metrics.get_parameter(name)
            assert parameter.value == value
            assert parameter.type_ == (Parameter.Type.BOOL if isinstance(value, bool)
                                       else Parameter.Type.DOUBLE)
    finally:
        if metrics is not None:
            metrics.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
def test_acceleration_world_is_open_and_includes_stopping_lane(
    perception, estimator, monkeypatch, tmp_path,
):
    world = tmp_path / 'acceleration.sdf'
    includes = []
    for side in (-1., 1.):
        for index, x in enumerate((-7., -1., 1., 74., 76., 150.)):
            color = 'blue' if side > 0 else 'yellow'
            if index in (1, 2, 3, 4):
                color = 'orange_large'
            if index == 5:
                color = 'orange_small'
            marker_index = index + (100 if side > 0 else 0)
            includes.append(f'<include><name>cone_{color}_{marker_index}</name>'
                            f'<pose>{x} {side*2.6} 0 0 0 0</pose></include>')
    world.write_text('<sdf><world>' + ''.join(includes) + '</world></sdf>')
    spec = importlib.util.spec_from_file_location(
        'gazebo_acceleration_metrics', Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: str(world))
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: '/tmp/lhr_gazebo')
    context = LaunchContext()
    context.launch_configurations.update(perception=perception, estimator=estimator,
                                         mission='acceleration', track_style='acceleration')
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    node = next(action for action in module._launch_setup(context)
                if isinstance(action, Node) and action.node_executable == 'metrics_node')
    params = evaluate_parameters(context, node._Node__parameters)[0]
    assert params['reference_closed'] is False
    args = ['--ros-args']
    for name, value in params.items():
        args += ['-p', f'{name}:={json.dumps(value)}']
    rclpy.init(args=args)
    metrics = None
    try:
        metrics = MetricsNode()
        assert not metrics._reference.closed
        assert metrics._reference.length == 157.
        assert metrics._reference.project(110., 0.)[0] == 0.
    finally:
        if metrics is not None:
            metrics.destroy_node()
        rclpy.shutdown()
