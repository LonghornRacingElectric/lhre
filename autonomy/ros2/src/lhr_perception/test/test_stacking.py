"""Verify independent evidence, bounded history and registration under motion."""

from lhr_perception.lidar_cone_detector import LidarConeDetector
import numpy as np
import pytest
import rclpy
from test_detector import cloud_in_sensor, mount_transform, odometry


@pytest.fixture
def detector():
    rclpy.init()
    node = LidarConeDetector()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def test_sparse_scans_form_one_cone_while_vehicle_moves(detector):
    for i in range(3):
        mount = mount_transform(angles=(0., 0., 0.))
        odom = odometry(x=float(i), y=0., yaw=0.)
        mount.header.stamp.nanosec = odom.header.stamp.nanosec = i * 100000000
        detector._tf_buffer.set_transform(mount, 'test')
        detector._odom_cb(odom)
        cloud = cloud_in_sensor([[7. - i, 2., .08]], mount)
        cloud.header.stamp.nanosec = i * 100000000
        detector._cloud_cb(cloud)
        detector._process()
        assert len(detector._cone_map) == (1 if i == 2 else 0)
    assert detector._cone_map[0][:2] == pytest.approx([7., 2.], abs=1e-5)


def test_duplicate_scan_cannot_create_three_point_evidence(detector):
    for _ in range(5):
        stacked = detector._stack_scan(1000000000, np.array([[7., 2., .1]]))
    assert len(stacked) == 1
    assert not detector._cluster(stacked)


def test_empty_scan_expires_old_evidence_and_history_is_bounded(detector):
    detector._stack_scan(1000000000, np.array([[7., 2., .1]]))
    stacked = detector._stack_scan(1600000000, np.empty((0, 3)))
    assert stacked.shape == (0, 3)
    for i in range(30):
        detector._stack_scan(1700000000 + i, np.array([[7., 2., .1]]))
    assert len(detector._scan_history) == detector._stack_max_frames


def test_replay_restart_clears_evidence_and_previous_map(detector):
    detector._cone_map.append([7., 2., 1.])
    detector._stack_scan(2000000000, np.array([[7., 2., .1]]))
    points = detector._stack_scan(1000000000, np.array([[8., 2., .1]]))
    assert len(points) == 1
    assert not detector._cone_map


def test_zero_window_uses_only_current_scan(detector):
    detector._stack_window = 0.
    detector._stack_scan(1000000000, np.array([[7., 2., .1]]))
    assert len(detector._stack_scan(1100000000, np.array([[7., 2., .1]]))) == 1


def test_clustering_follows_transitive_neighbors_not_just_seed_radius(detector):
    points = np.array([[0., 0., .1], [.3, 0., .1], [.6, 0., .1], [3., 0., .1]])
    clusters = detector._cluster(points)
    assert len(clusters) == 1
    assert clusters[0] == pytest.approx(points[:3])
