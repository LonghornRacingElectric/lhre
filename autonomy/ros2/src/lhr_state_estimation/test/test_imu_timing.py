"""IMU events between odometry ticks must contribute to the estimate."""

from types import SimpleNamespace

from lhr_state_estimation.ekf_node import EkfNode
from nav_msgs.msg import Odometry
import numpy as np
import pytest
import rclpy
from sensor_msgs.msg import Imu


@pytest.fixture
def node():
    rclpy.init(args=['--ros-args', '-p', 'publish_tf:=false'])
    instance = EkfNode()
    try:
        yield instance
    finally:
        instance.destroy_node()
        rclpy.shutdown()


def sample(stamp_ns, accel):
    msg = Imu()
    msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(stamp_ns, 1_000_000_000)
    msg.linear_acceleration.x = float(accel)
    return msg


def test_impulse_between_publish_ticks_is_integrated(node):
    """A 1 ms triangle with unit impulse must change speed by 1 m/s."""
    node._imu_cb(sample(1_000_000_000, 0))
    node._step()
    node._imu_cb(sample(1_001_000_000, 1000))
    node._imu_cb(sample(1_002_000_000, 0))
    node._imu_cb(sample(1_020_000_000, 0))
    node._step()
    assert node._ekf.twist[0] == pytest.approx(1.0)


def test_publishing_does_not_repeat_stale_acceleration_or_refresh_stamp(node, monkeypatch):
    """During a dropout, output retains the last sensor state and its age."""
    node._imu_cb(sample(1_000_000_000, 2))
    node._imu_cb(sample(1_010_000_000, 2))
    state = node._ekf.x.copy()
    messages = []
    monkeypatch.setattr(node, '_odom_pub', SimpleNamespace(publish=messages.append))
    for _ in range(10):
        node._step()
    np.testing.assert_array_equal(node._ekf.x, state)
    assert len(messages) == 10
    assert all(m.header.stamp.sec == 1 and m.header.stamp.nanosec == 10_000_000
               for m in messages)


def test_duplicate_and_out_of_order_samples_cannot_reapply_impulse(node):
    node._imu_cb(sample(1_000_000_000, 0))
    node._imu_cb(sample(1_001_000_000, 1000))
    state = node._ekf.x.copy()
    node._imu_cb(sample(1_001_000_000, 1000))
    node._imu_cb(sample(999_000_000, 1000))
    np.testing.assert_array_equal(node._ekf.x, state)
    node._imu_cb(sample(1_002_000_000, 0))
    assert node._ekf.twist[0] == pytest.approx(1.0)


def test_initial_pose_does_not_inherit_acceleration_before_initialization(node):
    node._awaiting_init = True
    node._imu_cb(sample(1_000_000_000, 1000))
    assert node._last_imu_stamp_ns is None
    pose = Odometry()
    pose.pose.pose.orientation.w = 1.0
    node._init_pose_cb(pose)
    node._imu_cb(sample(2_000_000_000, 0))
    node._imu_cb(sample(2_001_000_000, 0))
    assert node._ekf.twist[0] == 0


def test_no_sensor_sample_means_no_fresh_odometry(node, monkeypatch):
    messages = []
    monkeypatch.setattr(node, '_odom_pub', SimpleNamespace(publish=messages.append))
    node._step()
    assert messages == []
