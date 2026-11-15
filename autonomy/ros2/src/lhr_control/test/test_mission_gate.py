"""pure_pursuit commands speed only while DRIVING and not yet complete."""

import math

from lhr_control.pursuit_node import PurePursuit
import pytest
import rclpy
from std_msgs.msg import Bool, String


class _Capture:
    """Stands in for the command publisher."""

    def __init__(self):
        self.msgs = []

    def publish(self, msg):
        self.msgs.append(msg)


@pytest.fixture
def pp():
    rclpy.init()
    try:
        node = PurePursuit()
        # A straight path ahead of a car at the origin, facing +x.
        node._path = [(float(x), 0.0) for x in range(40)]
        node._have_odom = True
        node._cmd_pub = _Capture()
        node._v_prev = 5.0
        yield node
        node.destroy_node()
    finally:
        rclpy.shutdown()


def _speed(node):
    node._control_loop()
    return node._cmd_pub.msgs[-1].drive.speed


def test_drives_while_driving(pp):
    pp._status_cb(String(data='DRIVING'))
    assert _speed(pp) > 0.0


def test_brakes_once_the_mission_is_complete(pp):
    pp._status_cb(String(data='DRIVING'))
    pp._complete_cb(Bool(data=True))
    assert _speed(pp) == 0.0
    assert pp._v_prev == 0.0


def test_keeps_steering_while_braking(pp):
    # A left-hand arc: braking must still follow it, not straighten out.
    pp._path = [(20.0 * math.sin(a / 20.0), 20.0 * (1.0 - math.cos(a / 20.0)))
                for a in range(40)]
    pp._status_cb(String(data='DRIVING'))
    pp._complete_cb(Bool(data=True))
    pp._control_loop()
    cmd = pp._cmd_pub.msgs[-1].drive
    assert cmd.speed == 0.0
    assert cmd.steering_angle > 0.05


def test_braking_without_a_path_still_commands_a_stop(pp):
    pp._path = []
    pp._status_cb(String(data='DRIVING'))
    pp._complete_cb(Bool(data=True))
    pp._control_loop()
    assert pp._cmd_pub.msgs and pp._cmd_pub.msgs[-1].drive.speed == 0.0


def test_stopped_outside_driving(pp):
    pp._status_cb(String(data='READY'))
    assert _speed(pp) == 0.0


@pytest.fixture
def waiting():
    rclpy.init(args=['--ros-args', '-p', 'wait_for_mission:=true'])
    try:
        node = PurePursuit()
        node._path = [(float(x), 0.0) for x in range(40)]
        node._have_odom = True
        node._cmd_pub = _Capture()
        yield node
        node.destroy_node()
    finally:
        rclpy.shutdown()


def test_waits_for_the_mission_manager_before_moving(waiting):
    # A path and odometry before the manager's first status must not move
    # the car when a launch says a manager is coming.
    assert _speed(waiting) == 0.0
    waiting._status_cb(String(data='DRIVING'))
    assert _speed(waiting) > 0.0


def test_without_a_manager_it_still_runs_freely(pp):
    pp._v_prev = 0.0
    assert _speed(pp) > 0.0
