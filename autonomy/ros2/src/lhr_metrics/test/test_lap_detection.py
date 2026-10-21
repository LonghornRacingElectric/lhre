"""A moving planner path must not move the lap start or end the run early."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

from lhr_metrics.metrics_node import MetricsNode
from nav_msgs.msg import Odometry
import pytest
import rclpy


@pytest.mark.parametrize('speed', [4.0, 12.0])
def test_rolling_path_requires_return_to_fixed_origin(speed):
    state = SimpleNamespace(
        _lap_origin=(20.0, 0.0), _path=[], _start_radius=2.0,
        _start_hyst=1.0, _left_start=False, _lap_start_time=0.0,
        _min_lap_time=5.0, _lap_completed=False, _lap_pub=Mock(),
        get_logger=Mock(), finish=Mock(),
    )
    # The planner continually starts near the car, including after the
    # minimum lap time. Only returning to the first odometry pose counts.
    for index in range(1, 101):
        angle = index * 2.0 * math.pi / 100
        x, y = 20.0 * math.cos(angle), 20.0 * math.sin(angle)
        state._path = [(x, y), (x + 1.0, y)]
        MetricsNode._update_lap_detection(state, x, y, 20.0 * angle / speed)
        if index <= 96:
            assert not state._lap_completed
        if state._lap_completed:
            break
    assert state._lap_completed
    state._lap_pub.publish.assert_called_once()
    state.finish.assert_called_once_with('lap')


def test_first_scored_odometry_fixes_origin_and_timeout_defaults_off():
    rclpy.init()
    node = MetricsNode()
    try:
        assert node.timeout_sec == 0.0
        node._path = [(100.0, 100.0), (101.0, 100.0)]
        odom = Odometry()
        odom.header.stamp.sec = 1
        odom.pose.pose.position.x = 7.0
        odom.pose.pose.position.y = 3.0
        node._odom_cb(odom)
        assert node._lap_origin == (7.0, 3.0)
        node._path = [(8.0, 3.0), (9.0, 3.0)]
        odom.header.stamp.sec = 2
        odom.pose.pose.position.x = 8.0
        node._odom_cb(odom)
        assert node._lap_origin == (7.0, 3.0)
    finally:
        node.destroy_node()
        rclpy.shutdown()
