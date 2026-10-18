"""Skidpad fitting, limited visibility, and bounded acquisition behavior."""

import math
from unittest.mock import Mock

from lhr_track_builder.skidpad_acquisition import (
    _entry_corridor, _fit_layout, _visible, ACQUISITION_DEFAULTS, SkidpadAcquisition)
from lhr_trackgen.publish_cones import generate_skidpad_track
from lhr_trackgen.skidpad_geometry import generate_skidpad_lanes
from nav_msgs.msg import Odometry
import numpy as np
import pytest
import rclpy
from rclpy.time import Time
from std_msgs.msg import String
from visualization_msgs.msg import Marker, MarkerArray


def test_view_has_forward_240_degree_sector_and_15m_radius():
    points = [(14, 0), (16, 0), (-1, 0), (-1, 2), (0, -14), (float('nan'), 0)]
    assert _visible(points, np.array([0, 0, 0]), 15, 240).tolist() == [
        [14, 0], [-1, 2], [0, -14]]
    assert _visible([(0, 15), (0, -15)], np.array([0, 0, math.pi / 2]), 15, 240).tolist() == [
        [0, 15]]


@pytest.mark.parametrize('yaw', [0.0, 0.7, -2.5])
def test_fit_recovers_noisy_transformed_layout_with_missing_cones(yaw):
    points = np.array(sum(generate_skidpad_track(), []))
    rotation = np.array([[math.cos(yaw), math.sin(yaw)], [-math.sin(yaw), math.cos(yaw)]])
    translation = np.array([8.0, -3.0])
    points = points @ rotation + translation
    pose = np.r_[translation - 16.8747 * rotation[1], yaw + math.pi / 2]
    points = _visible(points, pose, 15, 240)
    points += np.random.default_rng(4).normal(0, 0.025, points.shape)
    fit = _fit_layout(points, np.r_[translation + [0.8, -0.6], yaw + 0.08],
                      ACQUISITION_DEFAULTS)
    assert fit is not None
    assert fit['pose'][:2] == pytest.approx(translation, abs=0.10)
    assert fit['pose'][2] == pytest.approx(yaw, abs=0.02)


def test_fit_rejects_duplicate_support_one_circle_and_wrong_dimensions():
    left, right = generate_skidpad_track()
    assert _fit_layout([left[0]] * 40, [0, 0, 0], ACQUISITION_DEFAULTS) is None
    assert _fit_layout(left[:17], [0, 0, 0], ACQUISITION_DEFAULTS) is None
    points = np.array(left + right) * 1.4
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS) is None


def test_entry_corridor_requires_both_sides_and_stops_before_last_cones():
    small, _ = generate_skidpad_lanes()
    pose = np.array([0, -16.8747, math.pi / 2])
    points = _visible(small, pose, 15, 240)
    path, available = _entry_corridor(points, pose, 1.875, 0.682)
    assert np.max(np.abs(path[:, 0])) < 1e-9
    assert path[-1, 1] <= -8 - 1.875 - 1.0 + 1e-6
    assert available > 4
    assert _entry_corridor(points[points[:, 0] > 0], pose, 1.875, 0.682) is None
    assert _entry_corridor(points * [2, 1], pose, 1.875, 0.682) is None


@pytest.fixture
def acquisition():
    rclpy.init()
    node = SkidpadAcquisition()
    node._test_time = 10.0
    node.get_clock = lambda: Mock(now=lambda: Time(seconds=node._test_time))
    for name in ('_path_pub', '_limit_pub', '_state_pub', '_view_pub', '_layout_pub'):
        setattr(node, name, Mock())
    odom = Odometry()
    odom.header.frame_id = 'map'
    odom.header.stamp = Time(seconds=10).to_msg()
    odom.pose.pose.position.y = -16.8747
    odom.pose.pose.orientation.z = math.sin(math.pi / 4)
    odom.pose.pose.orientation.w = math.cos(math.pi / 4)
    node._odom_cb(odom)
    node._status_cb(String(data='DRIVING'))
    yield node
    node.destroy_node()
    rclpy.shutdown()


def _observations(include_circles=True):
    msg = MarkerArray()
    groups = []
    if include_circles:
        left, right = generate_skidpad_track()
        groups.extend([('left_cones', left), ('right_cones', right)])
    groups.append(('orange_small', generate_skidpad_lanes()[0]))
    for ns, points in groups:
        for index, (x, y) in enumerate(points):
            marker = Marker(ns=ns, id=index)
            marker.header.frame_id = 'map'
            marker.pose.position.x, marker.pose.position.y = float(x), float(y)
            msg.markers.append(marker)
    return msg


def test_approach_requires_go_and_stops_on_stale_inputs(acquisition):
    node = acquisition
    node._cones_cb(_observations(False))
    node._status_cb(String(data='READY'))
    node._tick()
    assert node._phase == 'APPROACH'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    node._status_cb(String(data='DRIVING'))
    node._tick()
    assert 0 < node._limit_pub.publish.call_args.args[0].data <= 0.5
    node._test_time += 0.6
    node._tick()
    assert node._phase == 'HOLD'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_fit_requires_new_frames_and_standstill_then_acquires(acquisition):
    node = acquisition
    node._cones_cb(_observations())
    node._tick()
    for _ in range(8):
        node._tick()
    assert node._stable == 1
    node._speed = 0.2
    for _ in range(4):
        node._cones_cb(_observations())
        node._tick()
    assert node._phase == 'HOLD'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    node._speed = 0
    for _ in range(7):
        node._test_time += 0.2
        node._odom_time = node.get_clock().now().nanoseconds / 1e9
        node._cones_cb(_observations())
        node._tick()
    assert node._phase == 'ACQUIRED'
    assert len(node._path_pub.publish.call_args.args[0].poses) == 1
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    # Once acquired, reconnecting sparse inputs must never restart creep.
    node._test_time += 1.0
    node._tick()
    node._odom_time = node.get_clock().now().nanoseconds / 1e9
    node._cones_cb(_observations(False))
    node._tick()
    assert node._phase == 'ACQUIRED'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_approach_budget_and_invalid_frame_prevent_motion(acquisition):
    node = acquisition
    node._cones_cb(_observations(False))
    node._distance = 4.6
    node._tick()
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    node._distance = 5
    node._tick()
    assert node._phase == 'HOLD'
    node._odom_cb(Odometry())
    assert node._fault
    node._distance = 0
    node._tick()
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_frozen_odometry_does_not_refresh_watchdog(acquisition):
    node = acquisition
    before = node._odom_time
    node._test_time += 1.0
    msg = Odometry()
    msg.header.frame_id = 'map'
    msg.header.stamp = Time(seconds=10).to_msg()
    msg.pose.pose.orientation.w = 1.0
    node._odom_cb(msg)
    assert node._odom_time == before
    node._cones_cb(_observations(False))
    node._tick()
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
