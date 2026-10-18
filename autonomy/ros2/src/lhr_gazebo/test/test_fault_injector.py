"""fault_injector: timing, corruption, and how the launch splices it in."""

import importlib.util
import math
from pathlib import Path

from ackermann_msgs.msg import AckermannDriveStamped
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.utilities import (
    normalize_to_list_of_substitutions,
    perform_substitutions,
)
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_gazebo.fault_injector import FaultClock, FaultInjector, FAULTS
from lhr_safety.monitors import Fault
from nav_msgs.msg import Odometry
import pytest
import rclpy
from rclpy.qos import ReliabilityPolicy


# ----- FaultClock ------------------------------------------------------------

def test_fault_waits_for_driving_then_the_delay():
    clock = FaultClock(after_s=30.0, duration_s=0.0)
    assert clock.update(0.0, driving=False) == ''
    assert clock.update(100.0, driving=False) == ''
    assert clock.update(100.0, driving=True) == ''
    assert clock.update(129.9, driving=True) == ''
    assert clock.update(130.0, driving=True) == 'start'
    assert clock.active
    assert clock.update(500.0, driving=False) == ''
    assert clock.active


def test_timed_fault_ends_once():
    clock = FaultClock(after_s=0.0, duration_s=3.0)
    clock.update(10.0, driving=True)
    assert clock.update(10.0, driving=True) == 'start'
    assert clock.update(12.9, driving=True) == ''
    assert clock.update(13.0, driving=True) == 'end'
    assert not clock.active
    assert clock.update(20.0, driving=True) == ''


# ----- the fault table -------------------------------------------------------

@pytest.mark.parametrize('kind', sorted(FAULTS))
def test_every_fault_names_a_real_safety_check(kind):
    fault = FAULTS[kind]
    assert fault.expect in Fault.__members__
    is_topic = fault.src is not None
    assert is_topic == (fault.dst is not None) == (fault.msg is not None)
    assert is_topic != (fault.process is not None)


# ----- corruption ------------------------------------------------------------

@pytest.fixture
def injector(request):
    rclpy.init(args=['--ros-args', '-p', f'fault:={request.param}'])
    try:
        node = FaultInjector()
        yield node
        node.destroy_node()
    finally:
        # Always, so one failure cannot poison every later rclpy test.
        rclpy.shutdown()


@pytest.mark.parametrize(
    'injector', sorted(k for k, f in FAULTS.items() if f.src), indirect=True)
def test_relay_is_reliable(injector):
    # A best-effort relay never connects to a reliable consumer, so the
    # car silently loses that input before the fault even starts.
    assert injector._out.qos_profile.reliability == ReliabilityPolicy.RELIABLE


@pytest.mark.parametrize('injector', ['odom_spike'], indirect=True)
def test_odom_spike_is_one_sample(injector):
    first = injector._corrupt(Odometry())
    assert first.twist.twist.linear.x == 410.0
    assert injector._corrupt(Odometry()).twist.twist.linear.x == 0.0


@pytest.mark.parametrize('injector', ['odom_nan'], indirect=True)
def test_odom_nan(injector):
    assert math.isnan(injector._corrupt(Odometry()).pose.pose.position.x)


@pytest.mark.parametrize('injector', ['lidar_drop'], indirect=True)
def test_lidar_drop_drops(injector):
    assert injector._corrupt(b'cloud bytes') is None


@pytest.mark.parametrize('injector', ['cmd_nan'], indirect=True)
def test_cmd_nan(injector):
    msg = AckermannDriveStamped()
    msg.drive.steering_angle = 0.2
    assert math.isnan(injector._corrupt(msg).drive.steering_angle)


@pytest.mark.parametrize('injector', ['steer_freeze'], indirect=True)
def test_steer_freeze_holds_the_angle_at_the_fault(injector):
    for angle in (0.1, 0.4, -0.3):
        msg = AckermannDriveStamped()
        msg.drive.steering_angle = angle
        assert injector._corrupt(msg).drive.steering_angle == 0.1


def test_unknown_fault_is_rejected():
    rclpy.init(args=['--ros-args', '-p', 'fault:=meteor'])
    try:
        with pytest.raises(ValueError):
            FaultInjector()
    finally:
        rclpy.shutdown()


# ----- launch wiring ---------------------------------------------------------

def _text(context, value):
    return perform_substitutions(
        context, normalize_to_list_of_substitutions(value))


def _launch(monkeypatch, **overrides):
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
    nodes = [a for a in module._launch_setup(context) if isinstance(a, Node)]
    remaps = {}
    for node in nodes:
        rules = [(_text(context, a), _text(context, b))
                 for a, b in node._Node__remappings]
        remaps[node.node_executable] = rules
    return context, nodes, remaps


def test_no_fault_changes_nothing(monkeypatch):
    _, nodes, remaps = _launch(monkeypatch, estimator='ekf', perception='lidar')
    assert 'fault_injector' not in remaps
    assert all('_raw' not in b and '_actuated' not in b
               for rules in remaps.values() for _, b in rules)


@pytest.mark.parametrize('fault, estimator, owner, rule', [
    ('odom_nan', 'ekf', 'ekf_node',
     ('/lhr/vehicle/odom', '/lhr/vehicle/odom_raw')),
    ('odom_spike', 'truth', 'parameter_bridge',
     ('/lhr/vehicle/odom', '/lhr/vehicle/odom_raw')),
    ('lidar_drop', 'ekf', 'parameter_bridge',
     ('/lhr/lidar/points', '/lhr/lidar/points_raw')),
    ('cmd_nan', 'ekf', 'pursuit_node',
     ('/lhr/vehicle/cmd', '/lhr/vehicle/cmd_raw')),
    ('steer_freeze', 'ekf', 'joint_cmd_adapter',
     ('/lhr/vehicle/cmd', '/lhr/vehicle/cmd_actuated')),
])
def test_topic_faults_splice_the_injector_in(monkeypatch, fault, estimator,
                                             owner, rule):
    context, nodes, remaps = _launch(
        monkeypatch, fault=fault, estimator=estimator, perception='lidar',
        fault_after_s='5', fault_duration_s='2')
    assert rule in remaps[owner]
    (inj,) = [n for n in nodes if n.node_executable == 'fault_injector']
    params = evaluate_parameters(context, inj._Node__parameters)[0]
    assert params['fault'] == fault
    assert params['fault_after_s'] == 5.0
    assert type(params['fault_after_s']) is float
    assert params['fault_duration_s'] == 2.0


def test_process_faults_need_no_rewiring(monkeypatch):
    _, nodes, remaps = _launch(monkeypatch, fault='ekf_kill', estimator='ekf')
    assert 'fault_injector' in remaps
    assert all('_raw' not in b for rules in remaps.values() for _, b in rules)


def test_unknown_fault_fails_the_launch(monkeypatch):
    with pytest.raises(RuntimeError):
        _launch(monkeypatch, fault='meteor')
