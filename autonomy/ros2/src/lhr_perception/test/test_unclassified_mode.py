"""Check that unlabeled LiDAR output keeps the corrected cone map."""

from types import SimpleNamespace

from lhr_perception.cone_side_classifier import UNKNOWN
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_perception.pose_history import Pose2D
import rclpy
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


def _scan(sec, nanosec, x_offset=0.0):
    header = Header()
    header.stamp.sec = sec
    header.stamp.nanosec = nanosec
    header.frame_id = 'lidar'
    return point_cloud2.create_cloud_xyz32(header, [
        (5.0 + x_offset, 2.0, 0.0),
        (5.0 + x_offset, -2.0, 0.0),
    ])


def test_unclassified_scans_still_deduplicate_and_publish_all_cones():
    """Turning off side inference must preserve mapping and marker IDs."""
    rclpy.init(args=['--ros-args', '-p', 'classify_sides:=false'])
    node = LidarConeDetector()
    published = []
    node._det_pub = SimpleNamespace(publish=published.append)
    try:
        assert node._classify_sides is False
        node._pose_history.add(Pose2D(0, 0.0, 0.0, 0.0))
        node._pose_history.add(Pose2D(2_000_000_000, 0.0, 0.0, 0.0))

        node._cloud_cb(_scan(1, 0))
        node._process()
        first_positions = [entry[:2] for entry in node._cone_map]
        assert len(first_positions) == 2
        assert [entry[2] for entry in node._cone_map] == [1.0, 1.0]

        node._cloud_cb(_scan(1, 500_000_000, x_offset=0.04))
        node._process()
        assert len(node._cone_map) == 2
        assert [entry[2] for entry in node._cone_map] == [2.0, 2.0]
        assert all(entry[0] > previous[0] for entry, previous in zip(
            node._cone_map, first_positions))
        assert node._cone_sides == [UNKNOWN, UNKNOWN]
        assert node._side_votes == []

        assert len(published) == 2
        assert [(marker.ns, marker.id) for marker in published[-1].markers] == [
            ('cones', 0), ('cones', 1),
        ]
    finally:
        node.destroy_node()
        rclpy.shutdown()
