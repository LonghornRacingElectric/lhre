"""Check competition envelopes and rendered marker semantics."""

from lhr_trackgen.cone_geometry import CONE_SPECS, cone_triangles
from lhr_trackgen.publish_cones import ConePublisher
import numpy as np
import pytest
import rclpy
from visualization_msgs.msg import Marker


@pytest.mark.parametrize('kind', list(CONE_SPECS))
def test_mesh_envelope_and_stripe_colors(kind):
    spec = CONE_SPECS[kind]
    faces, colors = cone_triangles(spec)
    vertices = np.asarray(faces).reshape(-1, 3)
    assert np.ptp(vertices, axis=0) == pytest.approx(
        [spec.base_width_m, spec.base_width_m, spec.height_m])
    assert vertices[:, 2].min() == 0.
    assert spec.stripe_color in colors
    assert spec.color in colors
    assert len(colors) == len(faces)


def test_cone_marker_preserves_boundary_ids_and_ground_origin():
    rclpy.init()
    node = ConePublisher()
    try:
        marker = node.make_cone_marker(42, 'left_cones', 3., 4., 0., .2, 1.)
        assert marker.type == Marker.TRIANGLE_LIST
        assert marker.ns == 'left_cones' and marker.id == 42
        assert marker.pose.position.z == 0.
        assert marker.scale.x == marker.scale.y == marker.scale.z == 1.
        assert len(marker.points) == len(marker.colors)
        assert marker.text == 'blue'
        gate = node.make_cone_marker(20000, 'start_finish', 3., 4.,
                                     1., .35, 0., kind='orange_large')
        assert gate.text == 'orange_large'
        assert max(point.z for point in gate.points) == .505
    finally:
        node.destroy_node()
        rclpy.shutdown()
