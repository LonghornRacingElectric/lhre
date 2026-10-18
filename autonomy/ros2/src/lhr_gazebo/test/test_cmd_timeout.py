"""joint_cmd_adapter stops the wheels when commands stop."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_gazebo.joint_cmd_adapter import JointCmdAdapter
import pytest
import rclpy


class _Recorder:
    """Stands in for a publisher and keeps what was published."""

    def __init__(self):
        self.values = []

    def publish(self, msg):
        self.values.append(msg.data)


@pytest.fixture
def adapter(request):
    args = getattr(request, 'param', [])
    rclpy.init(args=['--ros-args', *args])
    node = JointCmdAdapter()
    wheels = [_Recorder() for _ in range(4)]
    (node._pub_vel_fl, node._pub_vel_fr,
     node._pub_vel_rl, node._pub_vel_rr) = wheels
    node._pub_steer_fl = node._pub_steer_fr = _Recorder()
    yield node, wheels
    node.destroy_node()
    rclpy.shutdown()


def test_nothing_happens_before_the_first_command(adapter):
    node, wheels = adapter
    node._watchdog(100.0)
    assert all(w.values == [] for w in wheels)


def test_wheels_stop_once_after_the_timeout(adapter):
    node, wheels = adapter
    node._apply(5.0, 0.0, 10.0)
    node._watchdog(10.0 + node._cmd_timeout - 0.01)
    assert all(len(w.values) == 1 for w in wheels)
    node._watchdog(10.0 + node._cmd_timeout + 0.01)
    assert all(w.values[-1] == 0.0 and len(w.values) == 2 for w in wheels)
    # Stopped once, not re-sent every tick.
    node._watchdog(11.0)
    assert all(len(w.values) == 2 for w in wheels)


def test_commands_resume_after_a_timeout(adapter):
    node, wheels = adapter
    node._apply(5.0, 0.0, 10.0)
    node._watchdog(11.0)
    node._apply(5.0, 0.0, 11.5)
    assert all(w.values[-1] > 0.0 for w in wheels)
    node._watchdog(11.5 + node._cmd_timeout + 0.01)
    assert all(w.values[-1] == 0.0 for w in wheels)


def test_clock_jumping_backwards_stops_the_wheels(adapter):
    # A Gazebo world reset rewinds sim time below the last command.
    node, wheels = adapter
    node._apply(5.0, 0.0, 50.0)
    node._watchdog(1.0)
    assert all(w.values[-1] == 0.0 for w in wheels)


@pytest.mark.parametrize('adapter', [['-p', 'cmd_timeout_s:=0.0']],
                         indirect=True)
def test_zero_disables_the_watchdog(adapter):
    node, _ = adapter
    assert node._cmd_timeout == 0.0
    assert len(node.timers) == 0


def _launch_params(monkeypatch, **overrides):
    """Evaluate the launch file and return the adapter's parameters."""
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
    (node,) = [a for a in module._launch_setup(context)
               if isinstance(a, Node) and a.node_executable == 'joint_cmd_adapter']
    return evaluate_parameters(context, node._Node__parameters)[0]


@pytest.mark.parametrize('override, expected', [(None, 0.3), ('1', 1.0)])
def test_launch_timeout_reaches_the_node_as_a_float(monkeypatch, override,
                                                    expected):
    overrides = {} if override is None else {'adapter_cmd_timeout_s': override}
    params = _launch_params(monkeypatch, **overrides)
    assert params['cmd_timeout_s'] == expected
    assert type(params['cmd_timeout_s']) is float
    rclpy.init(args=['--ros-args', '-p',
                     f'cmd_timeout_s:={params["cmd_timeout_s"]}'])
    node = JointCmdAdapter()
    try:
        assert node._cmd_timeout == expected
    finally:
        node.destroy_node()
        rclpy.shutdown()
