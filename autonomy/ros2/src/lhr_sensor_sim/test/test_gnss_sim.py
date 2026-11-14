"""Noisy GPS measurements retain sample time and never fabricate stale updates."""

from types import SimpleNamespace

from lhr_sensor_sim.gnss_sim_node import GnssSim
from nav_msgs.msg import Odometry
import pytest
import rclpy


def test_gnss_delay_and_distinct_source_samples(monkeypatch):
    rclpy.init()
    node = GnssSim()
    messages = []
    now = [1_000_000_000]
    monkeypatch.setattr(node, '_pub', SimpleNamespace(publish=messages.append))
    monkeypatch.setattr(node, 'get_clock', lambda: SimpleNamespace(
        now=lambda: SimpleNamespace(nanoseconds=now[0])))
    try:
        source = Odometry()
        source.header.stamp.sec = 1
        source.pose.pose.orientation.w = 1.
        node._truth_cb(source)
        node._truth_cb(source)
        node._flush()
        assert not messages
        now[0] = 1_050_000_000
        node._flush()
        assert len(messages) == 1
        assert messages[0].header.stamp.sec == 1
        assert messages[0].header.stamp.nanosec == 0
        assert messages[0].position_covariance[0] == pytest.approx(node._sigma[0]**2)
        now[0] += 10_000_000_000
        node._flush()
        assert len(messages) == 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
