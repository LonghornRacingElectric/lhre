"""Tests for geometry-assisted cone side inference."""

import math

from lhr_perception.cone_side_classifier import (
    classify_cone_sides,
    LEFT,
    RIGHT,
    StableSideLabels,
    UNKNOWN,
    vehicle_relative_side_vote,
)
from lhr_perception.pose_history import Pose2D
import pytest


def test_votes_from_vehicle_relative_lateral_position():
    """Use cones ahead of the vehicle as direct left and right evidence."""
    pose = Pose2D(0, 0.0, 0.0, 0.0)

    assert vehicle_relative_side_vote(2.5, 2.0, pose) > 0.0
    assert vehicle_relative_side_vote(2.5, -2.0, pose) < 0.0
    assert vehicle_relative_side_vote(-2.0, 2.0, pose) == 0.0
    assert vehicle_relative_side_vote(2.5, 0.25, pose) == 0.0


def test_shares_votes_across_aligned_boundary_gap():
    """Correct a weak outlier across a slightly enlarged along-track gap."""
    positions = [
        (0.0, 1.75), (2.0, 1.75), (4.0, 1.75), (7.1, 1.75),
        (0.0, -1.75), (2.0, -1.75), (4.0, -1.75), (7.1, -1.75),
    ]
    votes = [1.0, 1.0, 1.0, -0.05, -1.0, -1.0, -1.0, -1.0]

    sides = classify_cone_sides(positions, votes)

    assert sides == [LEFT] * 4 + [RIGHT] * 4


def test_does_not_bridge_a_perpendicular_cross_track_gap():
    """Reject a short link that crosses between two track boundaries."""
    positions = [
        (0.0, 1.55), (2.0, 1.55), (4.0, 1.55),
        (0.0, -1.55), (2.0, -1.55), (4.0, -1.55),
    ]
    votes = [1.0, 1.0, 1.0, -1.0, -1.0, -1.0]

    sides = classify_cone_sides(positions, votes)

    assert sides == [LEFT] * 3 + [RIGHT] * 3


def test_preserves_unknown_without_side_evidence():
    """Leave a boundary fragment unknown until the car observes its side."""
    positions = [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)]

    assert classify_cone_sides(positions, [0.0, 0.0, 0.0]) == [UNKNOWN] * 3


def test_rejects_mismatched_vote_count():
    """Reject state arrays that cannot correspond cone by cone."""
    with pytest.raises(ValueError):
        classify_cone_sides([(0.0, 0.0)], [])


def test_rotates_side_vote_with_vehicle_heading():
    """Interpret lateral position in the vehicle frame rather than the map."""
    pose = Pose2D(0, 0.0, 0.0, math.pi / 2.0)

    assert vehicle_relative_side_vote(-2.0, 2.5, pose) > 0.0
    assert vehicle_relative_side_vote(2.0, 2.5, pose) < 0.0


def test_stable_labels_require_independent_confirmations():
    """Ignore a one-observation proposal before locking a later side."""
    labels = StableSideLabels(confirmations=2)

    assert labels.update([LEFT, RIGHT]) == [UNKNOWN, UNKNOWN]
    assert labels.update([RIGHT, RIGHT]) == [UNKNOWN, RIGHT]
    assert labels.update([RIGHT, RIGHT]) == [RIGHT, RIGHT]


def test_stable_labels_seed_and_never_flip():
    """Seed the start corridor and keep its accepted labels immutable."""
    labels = StableSideLabels(confirmations=2)

    assert labels.update([LEFT, RIGHT], seed=True) == [LEFT, RIGHT]
    assert labels.update([RIGHT, LEFT]) == [LEFT, RIGHT]
    assert labels.labels(3) == [LEFT, RIGHT, UNKNOWN]
