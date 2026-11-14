"""Exercise startup calibration and corrected delayed history through the ROS node."""

import json
from types import SimpleNamespace

from lhr_state_estimation.ekf_node import EkfNode
from lhr_state_estimation.gnss import LocalENU
from lhr_vehicle import load_vehicle
import numpy as np
import pytest
import rclpy
from sensor_msgs.msg import Imu, NavSatFix
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


def stamped(time):
    header = Header()
    header.frame_id = 'lidar'
    header.stamp.sec = int(time)
    header.stamp.nanosec = round((time-int(time))*1e9)
    return header


@pytest.fixture
def node():
    rclpy.init(args=[
        '--ros-args', '-p', 'calibrate_gyro:=true',
        '-p', 'use_gnss:=true', '-p', 'gnss_heading_gain:=0.0',
        '-p', 'use_sim_time:=true', '-p', 'publish_tf:=false',
        '-p', 'gyro_calibration_settle_s:=0.0',
        '-p', 'gyro_calibration_duration_s:=0.5',
        '-p', 'gyro_calibration_timeout_s:=3.0',
        '-p', 'gyro_calibration_min_samples:=40'])
    instance = EkfNode()
    messages = []
    instance._odom_pub = SimpleNamespace(publish=messages.append)
    try:
        yield instance, messages
    finally:
        instance.destroy_node()
        rclpy.shutdown()


def imu(time, rate=.008):
    sample = Imu()
    sample.header = stamped(time)
    sample.angular_velocity.z = rate
    sample.linear_acceleration.z = 9.80665
    return sample


def calibrate(instance):
    rng = np.random.default_rng(2)
    points = rng.uniform([4., -6., .15], [14., 6., .35], (100, 3))
    points -= np.asarray(load_vehicle().lidar_position_m)
    for k in range(81):
        time = 1+k*.01
        instance._imu_cb(imu(time))
        if k % 10 == 0:
            instance._calibration_cloud_cb(point_cloud2.create_cloud_xyz32(stamped(time), points))


def test_no_odometry_before_calibration_then_corrected_gyro_is_used_in_delayed_history(node):
    instance, messages = node
    instance._imu_cb(imu(1.))
    instance._step()
    assert messages == [] and instance._last_imu_stamp_ns is None
    calibrate(instance)
    assert instance._calibration_applied
    assert instance._gyro_bias == pytest.approx(.008)
    instance._step()
    assert messages
    assert abs(instance._ekf.pose[2]) < 1e-10
    for record in instance._gnss_history.records:
        if record.gyro is not None:
            assert abs(record.gyro) < 1e-10
    before = instance._ekf.pose[2]
    fix = NavSatFix()
    fix.header = stamped(1.75)
    fix.header.frame_id = 'gnss_link'
    fix.status.status = 0
    fix.latitude, fix.longitude, fix.altitude = LocalENU(0, 0, 0).to_geodetic(1, 1, 0)
    fix.position_covariance = np.eye(3).ravel().tolist()
    fix.position_covariance_type = NavSatFix.COVARIANCE_TYPE_KNOWN
    status = []
    instance._gnss_status = SimpleNamespace(publish=status.append)
    instance._gnss_cb(fix)
    instance._imu_cb(imu(1.81))
    assert json.loads(status[-1].data)['accepted']
    assert instance._ekf.pose[0] > 0
    assert instance._ekf.pose[2] == pytest.approx(before, abs=1e-10)
    instance._imu_cb(imu(1.82, .108))
    instance._imu_cb(imu(1.83, .108))
    assert instance._ekf.twist[1] > .09


def test_no_lidar_evidence_never_completes_calibration(node):
    instance, messages = node
    for k in range(400):
        instance._imu_cb(imu(1+k*.01))
    instance._step()
    assert messages == []
    assert instance._calibration.state == 'failed'
