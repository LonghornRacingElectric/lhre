"""Check acceleration geometry against the 2026 event rules."""

import math

from lhr_trackgen.acceleration import (
    generate_acceleration_track, LARGE_CONE_SIZE, SMALL_CONE_SIZE,
)
import pytest


def test_acceleration_corridor():
    """Check length, clear width, spacing, direction, and deterministic output."""
    layout = generate_acceleration_track()
    assert layout == generate_acceleration_track()
    left, right = layout['left_cones'], layout['right_cones']
    assert left[0][0] == right[0][0] == 0.0
    assert left[-1][0] == right[-1][0] == 75.0
    for (lx, ly), (rx, ry) in zip(left, right):
        assert lx == rx
        assert ly > 0 > ry
        assert ly - ry - SMALL_CONE_SIZE[1] == pytest.approx(4.9)
    for row in (left, right):
        gaps = [b[0] - a[0] for a, b in zip(row, row[1:])]
        assert all(5.5 <= gap <= 6.5 for gap in gaps)
    for cones in layout.values():
        assert len(cones) == len(set(cones))
        assert all(math.isfinite(v) for point in cones for v in point)


def test_acceleration_lane_and_timing_markers():
    """Check orange entry/exit rows and gates around each timing line."""
    layout = generate_acceleration_track()
    small = layout['orange_small_cones']
    assert any(x < 0 for x, _ in small)
    assert max(x for x, _ in small) == 75.0 + 75.0
    assert all(x < 0 or 75 < x <= 150 for x, _ in small)
    large = layout['orange_large_cones']
    for line_x in (0.0, 75.0):
        for offset in (-1.0, 1.0):
            pair = sorted(y for x, y in large if x == line_x + offset)
            assert len(pair) == 2
            assert pair[1] - pair[0] - LARGE_CONE_SIZE[1] == pytest.approx(4.9)
    # Cone bases must not overlap, including around the timing markers.
    cones = [(x, y, SMALL_CONE_SIZE) for key, points in layout.items()
             if key != 'orange_large_cones' for x, y in points]
    cones += [(x, y, LARGE_CONE_SIZE) for x, y in large]
    for i, (x, y, size) in enumerate(cones):
        for ox, oy, other_size in cones[i + 1:]:
            assert (abs(x - ox) >= (size[0] + other_size[0]) / 2
                    or abs(y - oy) >= (size[1] + other_size[1]) / 2)


def test_acceleration_publication(monkeypatch):
    """Check namespaces, colors, IDs, and dimensions survive ROS publication."""
    rclpy = pytest.importorskip('rclpy')
    from lhr_trackgen.publish_cones import ConePublisher

    rclpy.init(args=['--ros-args', '-p', 'track_style:=acceleration'])
    node = None
    try:
        node = ConePublisher()
        messages = []
        monkeypatch.setattr(node.pub, 'publish', messages.append)
        node.on_timer()
        markers = messages[0].markers
        layout = generate_acceleration_track()
        assert len(markers) == sum(len(points) for points in layout.values())
        assert len({(m.ns, m.id) for m in markers}) == len(markers)
        for ns, points in layout.items():
            group = [m for m in markers if m.ns == ns]
            assert [(m.pose.position.x, m.pose.position.y) for m in group] == points
            size = LARGE_CONE_SIZE if ns == 'orange_large_cones' else SMALL_CONE_SIZE
            color = ((0.0, 0.2, 1.0) if ns == 'left_cones' else
                     (1.0, 1.0, 0.0) if ns == 'right_cones' else (1.0, 0.5, 0.0))
            for marker in group:
                assert (marker.scale.x, marker.scale.y, marker.scale.z) == size
                assert marker.pose.position.z == size[2] / 2
                assert (marker.color.r, marker.color.g, marker.color.b) == pytest.approx(color)
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()
