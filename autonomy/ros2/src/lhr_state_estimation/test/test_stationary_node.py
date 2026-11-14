"""Exercise scan-timed corrections, sensor loss and frame checks through ROS."""

import json
from types import SimpleNamespace

from lhr_state_estimation.ekf_node import EkfNode
from lhr_vehicle import load_vehicle
import numpy as np
import pytest
import rclpy
from sensor_msgs_py import point_cloud2
from test_live_gyro_calibration import calibrate, imu, stamped


@pytest.fixture
def node(monkeypatch):
    rclpy.init(args=[
        '--ros-args', '-p', 'use_gnss:=true', '-p', 'calibrate_gyro:=true',
        '-p', 'use_stationary_updates:=true', '-p', 'use_sim_time:=true',
        '-p', 'publish_tf:=false', '-p', 'gyro_calibration_settle_s:=0.0',
        '-p', 'gyro_calibration_duration_s:=0.5', '-p', 'gyro_calibration_timeout_s:=3.0',
        '-p', 'gyro_calibration_min_samples:=40', '-p', 'stationary_duration_s:=0.5'])
    instance = EkfNode()
    messages = []
    monkeypatch.setattr(instance, '_stationary_pub', SimpleNamespace(publish=messages.append))
    now = SimpleNamespace(nanoseconds=0)
    monkeypatch.setattr(instance, 'get_clock', lambda: SimpleNamespace(now=lambda: now))
    try:
        calibrate(instance)
        points = np.random.default_rng(2).uniform([4, -6, .15], [14, 6, .35], (100, 3))
        points -= np.asarray(load_vehicle().lidar_position_m)
        for k in range(101):
            t = 2+k*.01
            now.nanoseconds = round(t*1e9)
            instance._imu_cb(imu(t))
            if k % 10 == 0:
                instance._stationary_cloud_cb(point_cloud2.create_cloud_xyz32(stamped(t), points))
        yield instance, messages, points, now
    finally:
        instance.destroy_node()
        rclpy.shutdown()


def test_confirmed_standstill_corrects_speed_and_keeps_scan_subscription(node):
    instance, messages, _, _ = node
    assert any(json.loads(m.data)['applied'] for m in messages)
    assert instance._gnss_history.last_speed_ns == 3_000_000_000
    instance._step()
    assert instance._calibration_subscription is None
    assert '/lhr/lidar/points' in [s.topic_name for s in instance.subscriptions]
    assert '/lhr/vehicle/odom_truth' not in [s.topic_name for s in instance.subscriptions]


@pytest.mark.parametrize('fault', ['frame', 'stale', 'motion'])
def test_bad_cloud_revokes_stationary_corrections(node, fault):
    instance, messages, points, now = node
    header = stamped(3.1)
    now.nanoseconds = 3_100_000_000
    instance._imu_cb(imu(3.1))
    if fault == 'frame':
        header.frame_id = 'map'
    elif fault == 'stale':
        now.nanoseconds = 4_000_000_000
    else:
        points = points-np.array([.2, 0, 0])
    instance._stationary_cloud_cb(point_cloud2.create_cloud_xyz32(header, points))
    assert not instance._stationary.ready
    assert not json.loads(messages[-1].data)['applied']
    assert instance._gnss_history.last_speed_ns == 3_000_000_000


def test_cloud_loss_does_not_keep_clamping_speed(node):
    instance, _, _, _ = node
    for k in range(1, 101):
        sample = imu(3+k*.01)
        sample.linear_acceleration.x = .5
        instance._imu_cb(sample)
    assert not instance._stationary.ready
    assert instance._ekf.twist[0] > .4
    assert instance._gnss_history.last_speed_ns == 3_000_000_000
