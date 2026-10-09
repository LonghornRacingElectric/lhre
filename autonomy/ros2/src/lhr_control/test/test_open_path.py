"""Check local-path endpoints, freshness, and forward-only targets."""

from geometry_msgs.msg import PoseStamped
from lhr_control.pursuit_node import PurePursuit
from nav_msgs.msg import Path
import pytest
import rclpy
from rclpy.duration import Duration


class Capture:
    """Capture control commands."""

    def __init__(self):
        self.messages = []

    def publish(self, message):
        """Keep the last command."""
        self.messages.append(message)


@pytest.fixture
def controller():
    rclpy.init()
    node = PurePursuit()
    node._closed_path = False
    node._have_odom = True
    node._cmd_pub = Capture()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def set_path(controller, points):
    path = Path()
    for x, y in points:
        pose = PoseStamped()
        pose.pose.position.x, pose.pose.position.y = float(x), float(y)
        path.poses.append(pose)
    controller._path_cb(path)


def test_open_path_does_not_wrap_to_a_point_behind_the_car(controller):
    set_path(controller, [(0., 0.), (2., 0.), (4., 0.)])
    controller._x = 5.
    assert controller._find_lookahead() is None
    controller._control_loop()
    assert controller._cmd_pub.messages[-1].drive.speed == 0.


def test_short_path_uses_its_forward_endpoint(controller):
    set_path(controller, [(0., 0.), (1., 0.), (2., 0.)])
    controller._x = 1.5
    assert controller._find_lookahead() == (2, 2., 0.)


def test_empty_path_stops_instead_of_leaving_previous_command_active(controller):
    controller._v_prev = 8.
    set_path(controller, [])
    controller._control_loop()
    assert controller._cmd_pub.messages[-1].drive.speed == 0.
    assert controller._v_prev == 0.


def test_stale_local_path_stops(controller):
    set_path(controller, [(0., 0.), (5., 0.), (10., 0.)])
    controller._path_received_at -= Duration(seconds=2.)
    controller._control_loop()
    assert controller._cmd_pub.messages[-1].drive.speed == 0.


def test_open_curvature_does_not_connect_path_ends(controller):
    set_path(controller, [(0., 0.), (1., 0.), (2., 0.), (100., 100.)])
    controller._curv_win = 1
    assert controller._estimate_curvature(0) == 0.


def test_open_path_accelerates_without_jumping_to_minimum_speed(controller):
    set_path(controller, [(0., 0.), (5., 0.), (10., 0.)])
    controller._control_loop()
    assert controller._cmd_pub.messages[-1].drive.speed == pytest.approx(0.1)
