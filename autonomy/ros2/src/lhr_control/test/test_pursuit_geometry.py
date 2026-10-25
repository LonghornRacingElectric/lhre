"""Analytic steering and speed regressions through the production control callback."""

import math

from geometry_msgs.msg import PoseStamped
from lhr_control.pursuit_node import PurePursuit
from nav_msgs.msg import Odometry, Path
import pytest
import rclpy
from std_msgs.msg import String


class Capture:
    """Keep published commands for numerical assertions."""

    def __init__(self):
        self.messages = []

    def publish(self, message):
        """Record a command without spinning timers or requiring DDS discovery."""
        self.messages.append(message)


@pytest.fixture
def controller():
    rclpy.init()
    node = PurePursuit()
    node._cmd_pub = Capture()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def set_pose(node, x=0., y=0., yaw=0.):
    message = Odometry()
    message.pose.pose.position.x = float(x)
    message.pose.pose.position.y = float(y)
    message.pose.pose.orientation.z = math.sin(yaw / 2.)
    message.pose.pose.orientation.w = math.cos(yaw / 2.)
    node._odom_cb(message)


def set_path(node, points):
    message = Path()
    for x, y in points:
        pose = PoseStamped()
        pose.pose.position.x, pose.pose.position.y = float(x), float(y)
        message.poses.append(pose)
    node._path_cb(message)


def set_circle(node, radius, direction=1, origin=(0., 0.), angle=0.):
    # A circle has exactly known curvature even when sampled as a polygon.
    points = []
    for index in range(100):
        theta = angle + direction * index * 2. * math.pi / 100
        points.append((origin[0] + radius * math.cos(theta),
                       origin[1] + radius * math.sin(theta)))
    set_path(node, points)
    set_pose(node, *points[0], yaw=angle + direction * math.pi / 2.)
    node._curv_win = 2


def command(node):
    node._control_loop()
    assert node._cmd_pub.messages
    return node._cmd_pub.messages[-1].drive


@pytest.mark.parametrize('yaw', [0., math.pi / 2., -math.pi / 2., math.pi])
def test_straight_ahead_steering_is_zero_in_each_map_heading(controller, yaw):
    x, y = 13., -7.
    set_pose(controller, x, y, yaw)
    set_path(controller, [(x + d * math.cos(yaw), y + d * math.sin(yaw))
                          for d in (0., 5., 10.)])
    assert command(controller).steering_angle == pytest.approx(0., abs=1e-12)


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('origin,angle', [((0., 0.), 0.), ((13., -7.), 1.2)])
def test_circle_steering_has_analytic_magnitude_and_correct_sign(
        controller, direction, origin, angle):
    radius = 10.
    set_circle(controller, radius, direction, origin, angle)
    assert command(controller).steering_angle == pytest.approx(
        direction * math.atan(controller._L / radius), abs=1e-10)


@pytest.mark.parametrize('direction', [-1, 1])
def test_sharp_target_saturates_at_vehicle_steering_limit(controller, direction):
    set_pose(controller)
    set_path(controller, [(.2, direction * .2)])
    assert command(controller).steering_angle == pytest.approx(
        direction * controller.get_parameter('max_steer').value)


def test_closed_lookahead_wraps_forward_from_nearest_vertex(controller):
    set_path(controller, [(0., 0.), (5., 0.), (5., 5.), (0., 5.)])
    set_pose(controller, 0., 4.5, -math.pi / 2.)
    assert controller._find_closest_idx() == 3
    controller._ld = 4.
    assert controller._find_lookahead() == (0, 0., 0.)


@pytest.mark.parametrize('direction', [-1, 1])
@pytest.mark.parametrize('radius', [5., 10., 20.])
def test_circumcircle_curvature_matches_signed_inverse_radius(controller, radius, direction):
    set_circle(controller, radius, direction)
    for index in (0, 1, 50, 99):
        assert controller._estimate_curvature(index) == pytest.approx(direction / radius)


@pytest.mark.parametrize('points', [
    [], [(0., 0.)], [(0., 0.), (1., 0.)],
    [(0., 0.), (1., 0.), (2., 0.)], [(0., 0.), (0., 0.), (1., 0.)]])
def test_insufficient_collinear_and_duplicate_points_have_finite_zero_curvature(
        controller, points):
    set_path(controller, points)
    controller._curv_win = 1
    assert controller._estimate_curvature(0) == 0.


@pytest.mark.parametrize('direction', [-1, 1])
def test_circle_speed_obeys_lateral_acceleration_budget_in_both_directions(
        controller, direction):
    radius = 10.
    set_circle(controller, radius, direction)
    controller._v_prev = 8.
    # Remove only the transient rate limit, to measure the steady target.
    controller._max_accel = controller._max_decel = 1000.
    speed = command(controller).speed
    assert speed == pytest.approx(math.sqrt(controller._a_lat_max * radius))
    assert speed * speed / radius == pytest.approx(controller._a_lat_max)


def test_straight_speed_is_capped_and_curvature_epsilon_avoids_division_by_zero(controller):
    controller._closed_path = False
    set_pose(controller)
    set_path(controller, [(0., 0.), (100., 0.), (200., 0.)])
    controller._v_prev = controller._v_max
    assert command(controller).speed == controller._v_max


def test_tight_circle_preserves_configured_minimum_speed(controller):
    set_circle(controller, .1)
    controller._v_prev = controller._v_min
    assert command(controller).speed == controller._v_min


def test_open_straight_acceleration_is_bounded_on_each_control_tick(controller):
    controller._closed_path = False
    set_pose(controller)
    set_path(controller, [(0., 0.), (100., 0.), (200., 0.)])
    speeds = [command(controller).speed for _ in range(10)]
    assert speeds == pytest.approx([controller._max_accel * controller._dt * n
                                    for n in range(1, 11)])


def test_curve_transition_decelerates_at_the_configured_rate(controller):
    set_circle(controller, 1.)
    controller._v_prev = 10.
    speeds = [command(controller).speed for _ in range(10)]
    assert speeds == pytest.approx([10. - controller._max_decel * controller._dt * n
                                    for n in range(1, 11)])


def test_open_endpoint_speed_can_fall_below_minimum_to_stop(controller):
    controller._closed_path = False
    set_pose(controller)
    set_path(controller, [(0., 0.), (.2, 0.)])
    controller._v_prev = 1.
    assert command(controller).speed == pytest.approx(math.sqrt(2. * 3. * .2))


@pytest.mark.parametrize('radius,expected', [(10., 3.7), (1., 2.)])
def test_adaptive_lookahead_shortens_on_curves_and_respects_minimum(
        controller, radius, expected):
    set_circle(controller, radius)
    command(controller)
    assert controller._ld == pytest.approx(expected)


@pytest.mark.parametrize('status', ['READY', 'BRAKING', 'FINISHED', 'FAULT'])
def test_non_driving_mission_stops_and_resets_acceleration_history(controller, status):
    set_circle(controller, 10.)
    controller._v_prev = 8.
    controller._status_cb(String(data=status))
    drive = command(controller)
    assert drive.speed == drive.steering_angle == 0.
    assert controller._v_prev == 0.
    controller._closed_path = False
    controller._status_cb(String(data='DRIVING'))
    assert command(controller).speed == pytest.approx(controller._max_accel * controller._dt)
