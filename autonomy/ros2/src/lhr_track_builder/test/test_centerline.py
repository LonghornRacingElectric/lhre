"""Tests for centerline construction helpers."""

from lhr_track_builder.centerline import pair_classified_cones


def test_pairs_each_cone_at_most_once():
    """Prevent two nearby cones from producing duplicate center points."""
    left = [(0.0, 1.75), (2.0, 1.75), (4.0, 1.75)]
    right = [(0.0, -1.75), (2.0, -1.75), (4.0, -1.75)]

    midpoints = pair_classified_cones(left, right, 3.5, 1.0)

    assert sorted(midpoints) == [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)]


def test_ignores_unmatched_partial_boundary_cones():
    """Leave a cone unused when its opposite boundary is not observed yet."""
    left = [(0.0, 1.75), (2.0, 1.75), (20.0, 1.75)]
    right = [(0.0, -1.75), (2.0, -1.75)]

    midpoints = pair_classified_cones(left, right, 3.5, 1.0)

    assert sorted(midpoints) == [(0.0, 0.0), (2.0, 0.0)]


def test_width_gate_rejects_implausible_pair():
    """Reject a pairing outside the configured track-width band."""
    midpoints = pair_classified_cones(
        [(0.0, 1.75)], [(0.0, -8.0)], 3.5, 1.0)

    assert midpoints == []


def test_empty_boundary_has_no_centerline():
    """Wait until both track boundaries have been observed."""
    assert pair_classified_cones([], [(0.0, 0.0)], 3.5, 1.0) == []
