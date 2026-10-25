"""Check scan-time transforms, ground rejection, and missing-TF handling."""

from geometry_msgs.msg import TransformStamped
from lhr_perception.lidar_cone_detector import LidarConeDetector, transform_points
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import pytest
import rclpy
from scipy.spatial.transform import Rotation
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


@pytest.fixture
def detector():
    rclpy.init()
    node = LidarConeDetector()
    node._min_cluster_pts = 1
    yield node
    node.destroy_node()
    rclpy.shutdown()


def mount_transform(stamp=10, angles=(0.15, 0.3, -0.2)):
    tf = TransformStamped()
    tf.header.frame_id = 'base_link'
    tf.header.stamp.sec = stamp
    tf.child_frame_id = 'lidar'
    tf.transform.translation.x, tf.transform.translation.y, tf.transform.translation.z = (
        load_vehicle().lidar_position_m)
    q = Rotation.from_euler('xyz', angles).as_quat()
    tf.transform.rotation.x, tf.transform.rotation.y = float(q[0]), float(q[1])
    tf.transform.rotation.z, tf.transform.rotation.w = float(q[2]), float(q[3])
    return tf


def odometry(stamp=10, x=3.0, y=-2.0, yaw=0.7):
    msg = Odometry()
    msg.header.frame_id = 'map'
    msg.header.stamp.sec = stamp
    msg.child_frame_id = 'base_link'
    msg.pose.pose.position.x = x
    msg.pose.pose.position.y = y
    q = Rotation.from_euler('z', yaw).as_quat()
    msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = float(q[2]), float(q[3])
    return msg


def cloud_in_sensor(base_points, mount, stamp=10):
    q = mount.transform.rotation
    rotation = Rotation.from_quat([q.x, q.y, q.z, q.w])
    t = mount.transform.translation
    points = rotation.inv().apply(np.asarray(base_points) - [t.x, t.y, t.z])
    header = Header(frame_id='lidar')
    header.stamp.sec = stamp
    return point_cloud2.create_cloud_xyz32(header, points)


def test_full_transform_matches_independent_rotation():
    tf = mount_transform()
    points = np.array([[1.0, 2.0, 3.0], [-2.0, 1.0, 0.0]])
    expected = Rotation.from_euler('xyz', (0.15, 0.3, -0.2)).apply(points)
    expected += load_vehicle().lidar_position_m
    assert np.allclose(transform_points(points, tf.transform), expected)


def test_pitched_mount_filters_ground_body_and_out_of_range_returns(detector):
    mount = mount_transform()
    detector._tf_buffer.set_transform(mount, 'test')
    detector._odom_cb(odometry())
    base_points = [[7.0, 2.0, 0.25], [7.05, 2.0, 0.27],
                   [7.0, 1.0, 0.0], [0.0, 0.0, 0.25], [50.0, 2.0, 0.25]]
    cloud = cloud_in_sensor(base_points, mount)
    detector._cloud_cb(cloud)
    detector._process()
    assert len(detector._cone_map) == 1
    expected = Rotation.from_euler('z', 0.7).apply([7.025, 2.0, 0.26])[:2] + [3., -2.]
    assert detector._cone_map[0][:2] == pytest.approx(expected, abs=1e-5)


def test_delayed_cloud_uses_old_pose_and_old_mount(detector):
    detector._tf_buffer.set_transform(mount_transform(), 'test')
    detector._tf_buffer.set_transform(mount_transform(stamp=11, angles=(0., 0., 0.)), 'test')
    detector._odom_cb(odometry())
    detector._odom_cb(odometry(stamp=11, x=100., y=100., yaw=-1.))
    detector._cloud_cb(cloud_in_sensor([[7., 2., 0.25]], mount_transform()))
    detector._process()
    expected = Rotation.from_euler('z', 0.7).apply([7., 2., 0.25])[:2] + [3., -2.]
    assert detector._cone_map[0][:2] == pytest.approx(expected, abs=1e-5)


def test_missing_transform_retries_then_processes(detector):
    cloud = cloud_in_sensor([[7., 2., 0.25]], mount_transform())
    detector._cloud_cb(cloud)
    detector._process()
    assert not detector._cone_map
    assert detector._latest_cloud is cloud
    detector._tf_buffer.set_transform(mount_transform(), 'test')
    detector._odom_cb(odometry())
    detector._process()
    assert len(detector._cone_map) == 1
    assert detector._latest_cloud is None


def test_missing_transform_expires_without_latest_pose_fallback(detector):
    detector._cloud_cb(cloud_in_sensor([[7., 2., 0.25]], mount_transform()))
    detector._transform_wait_sec = -1.0
    detector._process()
    assert detector._latest_cloud is None
    assert not detector._cone_map


def test_zero_stamp_is_rejected_instead_of_using_latest_transform(detector):
    detector._cloud_cb(cloud_in_sensor([[7., 2., 0.25]], mount_transform(), stamp=0))
    detector._process()
    assert detector._latest_cloud is None
    assert not detector._cone_map


def test_vehicle_roll_and_pitch_are_preserved_in_map_transform(detector):
    mount = mount_transform()
    detector._tf_buffer.set_transform(mount, 'test')
    pose = odometry()
    rotation = Rotation.from_euler('xyz', (0.1, -0.15, 0.7))
    q = rotation.as_quat()
    orientation = pose.pose.pose.orientation
    orientation.x, orientation.y = float(q[0]), float(q[1])
    orientation.z, orientation.w = float(q[2]), float(q[3])
    detector._odom_cb(pose)
    detector._cloud_cb(cloud_in_sensor([[7., 2., 0.25]], mount))
    detector._process()
    expected = rotation.apply([7., 2., 0.25])[:2] + [3., -2.]
    assert detector._cone_map[0][:2] == pytest.approx(expected, abs=1e-5)


def test_nonfinite_returns_do_not_poison_clustering(detector):
    mount = mount_transform(angles=(0., 0., 0.))
    detector._tf_buffer.set_transform(mount, 'test')
    detector._odom_cb(odometry())
    x, y, z = load_vehicle().lidar_position_m
    header = Header(frame_id='lidar')
    header.stamp.sec = 10
    points = [[7. - x, 2. - y, 0.25 - z], [np.inf, 0., 0.], [np.nan, 0., 0.]]
    detector._cloud_cb(point_cloud2.create_cloud_xyz32(header, points))
    detector._process()
    assert len(detector._cone_map) == 1
    assert np.isfinite(detector._cone_map).all()
