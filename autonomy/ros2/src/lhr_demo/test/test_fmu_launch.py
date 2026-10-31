"""FMU launch flags reach the selected plant with stable ROS types."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_sim_fmu.sim_node import SimFmu
import pytest
import rclpy


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('override', [False, True])
def test_fmu_launch_startup_types_and_estimator_remapping(
        monkeypatch, perception, estimator, override):
    spec = importlib.util.spec_from_file_location(
        'fmu_launch', Path(__file__).parents[1]/'launch/mvs_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(plant='fmu', perception=perception, estimator=estimator)
    if override:
        context.launch_configurations.update(fmu_path='123', fmu_pedal_gain='1')
    simulator = next(action for action in module._launch_sim(context)
                     if isinstance(action, Node) and action.node_package == 'lhr_sim_fmu')
    params = evaluate_parameters(context, simulator._Node__parameters)[0]
    assert params['fmu_path'] == ('123' if override else '')
    assert type(params['pedal_gain']) is float
    assert params['pedal_gain'] == (1. if override else .1)
    simulator._perform_substitutions(context)
    if estimator == 'ekf':
        assert ('/lhr/vehicle/odom', '/lhr/vehicle/odom_truth') in (
            simulator.expanded_remapping_rules)
    captured = {}

    class Plant:
        def __init__(self, geometry, pose, path, *, pedal_gain):
            captured.update(path=path, gain=pedal_gain, pose=pose)
            self.provenance = {}

        def close(self):
            pass

    monkeypatch.setattr('lhr_sim_fmu.sim_node.FmuPlant', Plant)
    args = ['--ros-args']
    for name, value in params.items():
        args += ['-p', f'{name}:={json.dumps(value)}']
    rclpy.init(args=args)
    node = None
    try:
        node = SimFmu()
        assert captured['gain'] == params['pedal_gain']
        if override:
            assert captured['path'] == Path('123')
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()
