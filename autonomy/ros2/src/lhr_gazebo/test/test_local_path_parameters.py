"""Gazebo's forward paths need typed limits and open-path pursuit in both modes."""

from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

from geometry_msgs.msg import PoseStamped
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from nav_msgs.msg import Path as PathMessage
import pytest
import rclpy
from rclpy.duration import Duration
from rclpy.parameter import Parameter


@contextmanager
def _node(node_type, parameters):
    # No simulator clock runs in this startup test. All launch parameters
    # except this clock source are passed through exactly as evaluated.
    values = {**parameters, 'use_sim_time': False}
    args = ['--ros-args']
    for name, value in values.items():
        args += ['-p', f'{name}:={json.dumps(value)}']
    rclpy.init(args=args)
    node = None
    try:
        node = node_type()
        yield node
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


def _path(points):
    message = PathMessage()
    for x, y in points:
        pose = PoseStamped()
        pose.pose.position.x, pose.pose.position.y = float(x), float(y)
        message.poses.append(pose)
    return message


@pytest.mark.parametrize('perception,classify_sides', [
    ('sim', False), ('lidar', False), ('lidar', True),
])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('override', [False, True])
def test_local_path_launch_parameters(
    perception, classify_sides, estimator, override, monkeypatch,
):
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_local_paths', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(
        perception=perception, lidar_classify_sides=str(classify_sides).lower(),
        estimator=estimator)
    expected = {}
    with _node(PurePursuit, {}) as baseline:
        expected['path_timeout_sec'] = baseline.get_parameter('path_timeout_sec').value
        # Complete circuits still have a supported node-level closed mode.
        assert baseline.get_parameter('closed_path').value is True
    if override:
        # Whole-number CLI strings must reach double-declaring nodes as doubles.
        values = {'path_timeout_sec': '2'}
        context.launch_configurations.update(values)
        expected = {name: float(value) for name, value in values.items()}
    nodes = {node.node_executable: node for node in module._launch_setup(context)
             if isinstance(node, Node)}
    planner = evaluate_parameters(context, nodes['track_builder']._Node__parameters)[0]
    for name in ('planning_horizon_m', 'max_path_gap_m', 'max_path_turn_deg'):
        assert name not in context.launch_configurations
        assert name not in planner
    params = evaluate_parameters(context, nodes['pursuit_node']._Node__parameters)[0]
    assert params['path_timeout_sec'] == expected['path_timeout_sec']
    assert type(params['path_timeout_sec']) is float
    with _node(PurePursuit, params) as node:
        assert node.get_parameter('path_timeout_sec').type_ == Parameter.Type.DOUBLE
        assert params['closed_path'] is False
        assert node._closed_path is False
        assert node._path_timeout == expected['path_timeout_sec']
        # #92's straight prefix followed by a bend must not borrow the far end.
        node._path_cb(_path([(0, 0), (2, 0), (4, 0), (6, 0), (8, 0), (10, 0),
                             (12, 2), (12, 4), (10, 6), (8, 6), (6, 6)]))
        assert node._estimate_curvature(0) == 0.0
        node._cmd_pub = Mock()
        node._have_odom = True
        node._path_cb(_path([(0, 0), (2, 0), (4, 0)]))
        node._x = 5.0
        assert node._find_lookahead() is None
        node._control_loop()
        assert node._cmd_pub.publish.call_args.args[0].drive.speed == 0.0
        node._x = 0.0
        node._path_cb(_path([(0, 0), (5, 0), (10, 0)]))
        node._path_received_at -= Duration(seconds=node._path_timeout + 0.1)
        node._v_prev = 3.0
        node._control_loop()
        assert node._cmd_pub.publish.call_args.args[0].drive.speed == 0.0
        assert node._v_prev == 0.0
