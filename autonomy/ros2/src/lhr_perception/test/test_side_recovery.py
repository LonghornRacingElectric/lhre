"""Check motion-separated side evidence and marker replacement through the detector."""

from types import SimpleNamespace

from lhr_perception.cone_side_classifier import LEFT, RIGHT, UNKNOWN
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_perception.pose_history import Pose2D
import pytest
import rclpy
from visualization_msgs.msg import Marker


@pytest.fixture
def detector():
    rclpy.init()
    node = LidarConeDetector()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def test_stationary_start_and_duplicate_stamp_do_not_confirm_sides(detector):
    """Only genuinely new travelled viewpoints can establish a side label."""
    detector._cone_map = [[2., 1.5, 1.], [2., -1.5, 1.]]
    for stamp in range(1, 6):
        detector._update_side_classification(Pose2D(stamp, 0., 0., 0.))
    assert detector._cone_sides == [UNKNOWN, UNKNOWN]
    assert detector._side_votes == [0., 0.]
    detector._update_side_classification(Pose2D(6, 1., 0., 0.))
    assert detector._cone_sides == [UNKNOWN, UNKNOWN]
    detector._update_side_classification(Pose2D(6, 2., 0., 0.))
    assert detector._cone_sides == [UNKNOWN, UNKNOWN]
    detector._update_side_classification(Pose2D(7, 2., 0., 0.))
    assert detector._cone_sides == [LEFT, RIGHT]


def test_newly_mapped_cones_behind_vehicle_cannot_confirm_old_evidence(detector):
    """Travel without a new side observation must not count as confirmation."""
    detector._cone_map = [[2., 1.5, 1.]]
    detector._update_side_classification(Pose2D(1, 0., 0., 0.))
    detector._update_side_classification(Pose2D(2, 1., 0., 0.))
    detector._update_side_classification(Pose2D(3, 3., 0., 0.))
    detector._update_side_classification(Pose2D(4, 4., 0., 0.))
    assert detector._cone_sides == [UNKNOWN]
    detector._update_side_classification(Pose2D(5, 1., 0., 0.))
    assert detector._cone_sides == [UNKNOWN]


def test_corrective_viewpoints_replace_wrong_marker_namespace(detector):
    """A bad initial corridor can recover without leaving stale right markers."""
    detector._cone_map = [[x, y, 1.] for y in (1.5, -1.5) for x in (4., 6., 8.)]
    published = []
    detector._det_pub = SimpleNamespace(publish=published.append)
    for stamp, x in enumerate((0., 1., 2.), start=1):
        detector._update_side_classification(Pose2D(stamp, x, 3., 0.))
    assert detector._cone_sides[0] == RIGHT
    detector._publish_accumulated()
    for stamp, x in enumerate((3., 4., 5., 6.), start=4):
        detector._update_side_classification(Pose2D(stamp, x, 0., 0.))
    assert detector._cone_sides == [LEFT] * 3 + [RIGHT] * 3
    detector._publish_accumulated()
    assert {(m.ns, m.id) for m in published[-1].markers if m.action == Marker.DELETE} == {
        ('right_cones', 0), ('right_cones', 1), ('right_cones', 2)}
    assert {(m.ns, m.id) for m in published[-1].markers if m.action == Marker.ADD} == {
        ('left_cones', 0), ('left_cones', 1), ('left_cones', 2),
        ('right_cones', 3), ('right_cones', 4), ('right_cones', 5)}


def test_unobserved_boundaries_retain_evidence_when_only_one_side_is_visible(detector):
    """A new right-side observation must not erase the mapped left boundary's votes."""
    detector._cone_map = [[2., 1.5, 1.], [2., -1.5, 1.]]
    for stamp, x in enumerate((0., 1., 2.), start=1):
        detector._update_side_classification(Pose2D(stamp, x, 0., 0.))
    before = detector._side_votes.copy()
    detector._cone_map.append([22., -1.5, 1.])
    detector._update_side_classification(Pose2D(4, 20., 0., 0.))
    assert detector._side_votes[:2] == before


def test_distant_fresh_observation_cannot_confirm_a_stale_fragment(detector):
    """Evidence on another disconnected boundary cannot confirm an unseen cone."""
    detector._cone_map = [[2., 1.5, 1.], [22., -1.5, 1.]]
    for stamp, x in enumerate((0., 1., 20.), start=1):
        detector._update_side_classification(Pose2D(stamp, x, 0., 0.))
    assert detector._cone_sides == [UNKNOWN, UNKNOWN]
    detector._update_side_classification(Pose2D(4, 21., 0., 0.))
    assert detector._cone_sides == [UNKNOWN, RIGHT]
