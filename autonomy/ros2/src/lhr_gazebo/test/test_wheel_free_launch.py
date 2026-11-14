"""Wheel-free defaults must reach the actual EKF, in both perception branches."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_state_estimation.ekf_node import EkfNode
import pytest
import rclpy
from sensor_msgs.msg import JointState


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('enabled', [None, False, True])
def test_wheel_fusion_reaches_ekf_and_subscription(monkeypatch, perception, enabled):
    pkg = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        'wheel_free', pkg / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: str(pkg))
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(perception=perception, estimator='ekf')
    if enabled is not None:
        context.launch_configurations['use_wheel_speeds'] = str(enabled).lower()
    expected = enabled is True
    nodes = [a for a in module._launch_setup(context) if isinstance(a, Node)]
    assert any(n.node_executable == 'inertial_sim' for n in nodes) is expected
    ekf = next(n for n in nodes if n.node_executable == 'ekf_node')
    params = evaluate_parameters(context, ekf._Node__parameters)[0]
    assert params['use_wheel_speeds'] is expected
    rclpy.init(args=['--ros-args', '-p', f'use_wheel_speeds:={json.dumps(expected)}'])
    node = EkfNode()
    try:
        wheel_topics = [s.topic_name for s in node.subscriptions
                        if s.topic_name == '/lhr/sensor/wheel_speeds']
        assert bool(wheel_topics) is expected
        node._wheel_cb(JointState(name=['rl', 'rr'], velocity=[100., 100.]))
        assert bool(node._ekf.twist[0] > 0) is expected
    finally:
        node.destroy_node()
        rclpy.shutdown()
