"""Check boundary interpolation, frame handling, and planner failure behavior."""

import math

from lhr_track_builder.acceleration_planner import _mean_boundary_path, AccelerationPlanner
from nav_msgs.msg import Odometry
import pytest
import rclpy
from std_msgs.msg import String
from visualization_msgs.msg import Marker, MarkerArray


def test_mean_of_independently_interpolated_boundaries():
    """Unequal sampling and duplicate detections must not bias the mean path."""
    left = [(0, 2.5), (6, 3.1), (12, 3.7), (6, 3.1)]
    right = [(1, -2.4), (4, -2.1), (10, -1.5)]
    path = _mean_boundary_path(left, right, 1, 10, 0.5)
    assert path[0][0] == 1
    assert path[-1][0] == 10
    for x, y in path:
        assert y == pytest.approx(0.1 * x)
    assert all(0 < b[0] - a[0] <= 0.5 for a, b in zip(path, path[1:]))


@pytest.mark.parametrize('left,right', [
    ([], [(0, -2.5), (6, -2.5)]),
    ([(0, 2.5), (0, 2.6)], [(0, -2.5), (6, -2.5)]),
    ([(0, 2.5), (6, 2.5)], [(0, 2.0), (6, 2.0)]),
    ([(0, float('nan')), (6, 2.5)], [(0, -2.5), (6, -2.5)]),
])
def test_invalid_boundaries(left, right):
    """Missing, degenerate, nonfinite, and crossing boundaries cannot form a path."""
    assert _mean_boundary_path(left, right, 0, 10, 0.5) == []


def test_extrapolation_is_bounded():
    """Do not invent the entire course from the first visible cones."""
    path = _mean_boundary_path([(5, 2.5), (10, 2.5)],
                               [(6, -2.5), (12, -2.5)], -20, 100, 0.5)
    assert path[0][0] == 3
    assert path[-1][0] == 13


@pytest.mark.parametrize('value', ['0.5', '75.0', '.nan'])
def test_invalid_stop_distance(value):
    """Reject stops before the braking margin or beyond the permitted exit lane."""
    rclpy.init(args=['--ros-args', '-p', f'acceleration_stop_distance_m:={value}'])
    try:
        with pytest.raises(ValueError):
            AccelerationPlanner()
    finally:
        rclpy.shutdown()


def test_rotated_unclassified_cones_and_stale_inputs(monkeypatch):
    """Publish a world-frame mean from detections, then clear it on input loss."""
    rclpy.init(args=[])
    node = AccelerationPlanner()
    try:
        paths, completions = [], []
        monkeypatch.setattr(node._path_pub, 'publish', paths.append)
        monkeypatch.setattr(node._complete_pub, 'publish', completions.append)
        odom = Odometry()
        odom.header.frame_id = 'map'
        odom.pose.pose.position.x = 10.0
        odom.pose.pose.position.y = -5.0
        yaw = 0.4
        odom.pose.pose.orientation.z = math.sin(yaw / 2)
        odom.pose.pose.orientation.w = math.cos(yaw / 2)
        node._odom_cb(odom)
        markers = MarkerArray()
        for x, y in [(x, y) for x in (0., 6., 12.) for y in (-2.564, 2.564)]:
            marker = Marker()
            marker.header.frame_id = 'map'
            marker.ns = 'cones'
            marker.pose.position.x = 10 + math.cos(yaw) * x - math.sin(yaw) * y
            marker.pose.position.y = -5 + math.sin(yaw) * x + math.cos(yaw) * y
            markers.markers.append(marker)
        node._cones_cb(markers)
        node._plan()
        assert len(paths[-1].poses) > 2
        for pose in paths[-1].poses:
            dx = pose.pose.position.x - 10
            dy = pose.pose.position.y + 5
            assert -math.sin(yaw) * dx + math.cos(yaw) * dy == pytest.approx(0)
        assert not completions[-1].data
        odom.header.stamp.sec = 1
        moved = Odometry()
        moved.header.frame_id = 'map'
        moved.header.stamp.sec = 2
        moved.pose.pose.position.x = 10 + math.cos(yaw)
        moved.pose.pose.position.y = -5 + math.sin(yaw)
        node._odom_cb(moved)
        assert node._speed == pytest.approx(1.)  # twist remains zero
        node._cone_time = node._cone_time - rclpy.duration.Duration(seconds=2)
        node._plan()
        assert paths[-1].poses == []
        for marker in markers.markers:
            marker.header.frame_id = 'unrelated_frame'
        node._cones_cb(markers)
        node._plan()
        assert paths[-1].poses == []
        node._status_cb(String(data='DRIVING'))
        assert not any(msg.data for msg in completions)
    finally:
        node.destroy_node()
        rclpy.shutdown()
