"""The acquisition follower honors zero, stale-input stops, and finite paths."""

from unittest.mock import Mock

from lhr_control.pursuit_node import PurePursuit
from nav_msgs.msg import Path
import pytest
import rclpy
from rclpy.time import Time
from std_msgs.msg import Float32


@pytest.fixture
def controller():
    rclpy.init(args=['--ros-args', '-p', 'require_planner_limit:=true',
                     '-p', 'path_closed:=false'])
    node = PurePursuit()
    node._test_time = 10.0
    node.get_clock = lambda: Mock(now=lambda: Time(seconds=node._test_time))
    for name in ('_cmd_pub', '_la_pub', '_curv_pub', '_vcmd_pub'):
        setattr(node, name, Mock())
    node._mission_status = 'DRIVING'
    node._have_odom = True
    node._path_time = node._odom_time = 10.0
    node._path = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    yield node
    node.destroy_node()
    rclpy.shutdown()


def test_speed_cap_overrides_minimum_and_expires(controller):
    node = controller
    node._speed_limit_cb(Float32(data=0.5))
    node._control_loop()
    assert 0 < node._cmd_pub.publish.call_args.args[0].drive.speed <= 0.5
    node._test_time += 0.6
    node._control_loop()
    assert node._cmd_pub.publish.call_args.args[0].drive.speed == 0


@pytest.mark.parametrize('limit', [0.0, -1.0, float('nan')])
def test_invalid_or_zero_limit_stops(controller, limit):
    controller._speed_limit_cb(Float32(data=limit))
    controller._control_loop()
    assert controller._cmd_pub.publish.call_args.args[0].drive.speed == 0


def test_open_path_never_wraps_lookahead_or_curvature(controller):
    node = controller
    node._x = 2.9
    assert node._find_lookahead() == (3, 3, 0)
    assert node._estimate_curvature(3) == 0
    node._speed_limit_cb(Float32(data=0.5))
    node._path_cb(Path())
    node._control_loop()
    assert node._cmd_pub.publish.call_args.args[0].drive.speed == 0
