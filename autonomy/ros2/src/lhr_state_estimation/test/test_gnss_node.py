"""GNSS status/covariance validation and delayed correction at the ROS boundary."""

import json
from types import SimpleNamespace

from lhr_state_estimation.ekf_node import EkfNode
from lhr_state_estimation.gnss import LocalENU
import numpy as np
import pytest
import rclpy
from sensor_msgs.msg import Imu, NavSatFix


@pytest.fixture
def node():
    rclpy.init(args=['--ros-args', '-p', 'use_gnss:=true', '-p', 'publish_tf:=false'])
    instance = EkfNode()
    for stamp in [1., 1.05, 1.1]:
        msg = Imu()
        msg.header.stamp.sec = int(stamp)
        msg.header.stamp.nanosec = round((stamp-int(stamp))*1e9)
        instance._imu_cb(msg)
    messages = []
    instance._gnss_status = SimpleNamespace(publish=messages.append)
    try:
        yield instance, messages
    finally:
        instance.destroy_node()
        rclpy.shutdown()


def fix():
    msg = NavSatFix()
    msg.header.frame_id = 'gnss_link'
    msg.header.stamp.sec, msg.header.stamp.nanosec = 1, 50_000_000
    msg.status.status = 0
    msg.latitude, msg.longitude, msg.altitude = LocalENU(0, 0, 0).to_geodetic(1, 0, 0)
    msg.position_covariance = np.eye(3).ravel().tolist()
    msg.position_covariance_type = NavSatFix.COVARIANCE_TYPE_KNOWN
    return msg


def advance(node):
    sample = Imu()
    sample.header.stamp.sec, sample.header.stamp.nanosec = 1, 101_000_000
    node._imu_cb(sample)


def test_valid_delayed_fix_corrects_estimate_without_a_truth_subscription(node):
    instance, messages = node
    instance._gnss_cb(fix())
    advance(instance)
    assert instance._ekf.pose[0] > .1
    assert json.loads(messages[-1].data)['accepted']
    status = json.loads(messages[-1].data)
    assert status['current_yaw_delta_rad'] == 0.
    assert status['current_yaw_before_rad'] == status['current_yaw_after_rad']
    topics = [s.topic_name for s in instance.subscriptions]
    assert '/lhr/gnss/fix' in topics
    assert '/lhr/vehicle/odom_truth' not in topics
    assert '/lhr/sensor/wheel_speeds' not in topics


@pytest.mark.parametrize('kind', ['no_fix', 'unknown_covariance', 'wrong_frame', 'nan_covariance'])
def test_invalid_fix_cannot_modify_the_state(node, kind):
    instance, messages = node
    msg = fix()
    if kind == 'no_fix':
        msg.status.status = -1
    elif kind == 'unknown_covariance':
        msg.position_covariance_type = NavSatFix.COVARIANCE_TYPE_UNKNOWN
    elif kind == 'wrong_frame':
        msg.header.frame_id = 'unsurveyed_antenna'
    else:
        msg.position_covariance[0] = float('nan')
    before = instance._ekf.x.copy()
    instance._gnss_cb(msg)
    np.testing.assert_array_equal(before, instance._ekf.x)
    assert not json.loads(messages[-1].data)['accepted']


def test_delayed_replay_cannot_silently_discard_legacy_wheel_updates():
    rclpy.init(args=['--ros-args', '-p', 'use_gnss:=true', '-p', 'use_wheel_speeds:=true'])
    try:
        with pytest.raises(ValueError, match='use_wheel_speeds:=false'):
            EkfNode()
    finally:
        rclpy.shutdown()


@pytest.mark.parametrize('parameter,value', [
    ('gnss_heading_gain', '-0.1'), ('gnss_heading_gain', '1.1'),
    ('gnss_heading_gain', '.nan'), ('yaw_rate_variance', '0.0'),
    ('yaw_rate_variance', '.nan')])
def test_invalid_heading_controls_fail_before_running(parameter, value):
    rclpy.init(args=['--ros-args', '-p', f'{parameter}:={value}'])
    try:
        with pytest.raises(ValueError, match=parameter):
            EkfNode()
    finally:
        rclpy.shutdown()
