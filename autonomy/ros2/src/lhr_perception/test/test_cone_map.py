"""Tests for persistent cone-map data association."""

import math

from lhr_perception.cone_map import update_cone_map


def test_updates_nearest_cone_instead_of_first_candidate():
    """Select the nearest candidate regardless of insertion order."""
    cone_map = [[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]

    added = update_cone_map([(0.9, 0.0)], cone_map, 1.5)

    assert added == 0
    assert cone_map[0] == [0.0, 0.0, 1.0]
    assert math.isclose(cone_map[1][0], 0.95)
    assert cone_map[1][2] == 2.0


def test_each_existing_cone_is_used_once_per_scan():
    """Prevent two detections from updating one landmark in a single scan."""
    cone_map = [[0.0, 0.0, 3.0]]

    added = update_cone_map([(-0.1, 0.0), (0.4, 0.0)], cone_map, 1.0)

    assert added == 1
    assert len(cone_map) == 2
    assert math.isclose(cone_map[0][0], -0.025)
    assert cone_map[1] == [0.4, 0.0, 1.0]


def test_detection_outside_gate_creates_new_cone():
    """Keep separated landmarks distinct when no association is plausible."""
    cone_map = [[0.0, 0.0, 2.0]]

    added = update_cone_map([(2.0, 0.0)], cone_map, 1.5)

    assert added == 1
    assert cone_map[-1] == [2.0, 0.0, 1.0]


def test_empty_scan_leaves_map_unchanged():
    """Do not modify the persistent map when a scan has no cones."""
    cone_map = [[0.0, 0.0, 2.0]]

    added = update_cone_map([], cone_map, 1.5)

    assert added == 0
    assert cone_map == [[0.0, 0.0, 2.0]]
