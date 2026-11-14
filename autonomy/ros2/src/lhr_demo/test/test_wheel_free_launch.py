"""The kinematic launch must share the wheel-free EKF default and typed override."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
import pytest


@pytest.mark.parametrize('enabled', [None, 'false', 'true'])
def test_kinematic_wheel_mode_is_forwarded(enabled):
    spec = importlib.util.spec_from_file_location(
        'mvs_wheel_free', Path(__file__).parents[1] / 'launch/mvs_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations['estimator'] = 'ekf'
    if enabled is not None:
        context.launch_configurations['use_wheel_speeds'] = enabled
    ekf = next(a for a in module._launch_sim(context)
               if isinstance(a, Node) and a.node_executable == 'ekf_node')
    params = evaluate_parameters(context, ekf._Node__parameters)[0]
    assert params['use_wheel_speeds'] is (enabled == 'true')
