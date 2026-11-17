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


@pytest.mark.parametrize('width', [2.8, 3.0])
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_narrow_track_keeps_opposite_boundaries_separate(width, partial, reverse):
    """Short cross-track edges cannot spread one side through an unknown endpoint."""
    positions = [(2. * i, width / 2) for i in range(20)]
    positions += [(2. * i, -width / 2) for i in range(20)]
    votes = [1.] * 20 + [-.9] * 20
    if partial:
        votes = [0.] * 40
        votes[0], votes[-1] = 1., -.9
    expected = [LEFT] * 20 + [RIGHT] * 20
    if reverse:
        positions.reverse()
        votes.reverse()
        expected.reverse()
    assert classify_cone_sides(positions, votes) == expected


def test_aligned_gap_does_not_join_strong_opposing_components():
    """Alignment alone cannot override conflicting boundary observations."""
    positions = [(0., 0.), (2., 0.), (0., .2),
                 (5.1, 0.), (7.1, 0.), (7.1, .2)]
    assert classify_cone_sides(positions, [1.] * 3 + [-.9] * 3) == (
        [LEFT] * 3 + [RIGHT] * 3)


def test_fresh_evidence_supports_only_its_connected_boundary():
    """A fresh viewpoint supports neighbors, but cannot confirm another stale fragment."""
    positions = [(0., 1.5), (2., 1.5), (4., 1.5),
                 (20., -1.5), (22., -1.5), (24., -1.5)]
    assert classify_cone_sides(positions, [1.] * 3 + [-.9] * 3,
                               fresh_votes=[0., 0., 0., -.5, 0., 0.]) == (
        [UNKNOWN] * 3 + [RIGHT] * 3)


def test_fresh_evidence_length_must_match_the_map():
    """Reject a freshness vector that cannot identify the same mapped cones."""
    with pytest.raises(ValueError, match='fresh_votes'):
        classify_cone_sides([(0., 1.5)], [1.], fresh_votes=[])


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


def test_stable_labels_recover_only_after_sustained_correction():
    """Repeated opposing evidence corrects an accepted side without flicker."""
    labels = StableSideLabels(confirmations=2)

    assert labels.update([LEFT, RIGHT]) == [UNKNOWN, UNKNOWN]
    assert labels.update([LEFT, RIGHT]) == [LEFT, RIGHT]
    for _ in range(3):
        assert labels.update([RIGHT, LEFT]) == [LEFT, RIGHT]
    assert labels.update([RIGHT, LEFT]) == [RIGHT, LEFT]
    assert labels.update([LEFT, RIGHT]) == [RIGHT, LEFT]
    assert labels.labels(3) == [RIGHT, LEFT, UNKNOWN]


def test_missing_or_matching_evidence_interrupts_a_pending_flip():
    """Separated contradictory glimpses cannot add up to sustained correction."""
    labels = StableSideLabels(confirmations=1)
    assert labels.update([LEFT]) == [LEFT]
    for interruption in (UNKNOWN, LEFT):
        assert labels.update([RIGHT]) == [LEFT]
        assert labels.update([interruption]) == [LEFT]
    assert labels.update([RIGHT]) == [LEFT]
    assert labels.update([RIGHT]) == [RIGHT]


def test_new_map_entries_need_their_own_confirmations():
    """Map growth preserves accepted sides without seeding new IDs."""
    labels = StableSideLabels(confirmations=2)
    labels.update([LEFT, RIGHT])
    labels.update([LEFT, RIGHT])
    assert labels.labels(3) == [LEFT, RIGHT, UNKNOWN]
    assert labels.update([LEFT, RIGHT, LEFT]) == [LEFT, RIGHT, UNKNOWN]
    assert labels.update([LEFT, RIGHT, LEFT]) == [LEFT, RIGHT, LEFT]
