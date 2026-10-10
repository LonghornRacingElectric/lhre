"""Regression tests for progressive LiDAR autocross perception."""

import math
import random

from lhr_perception.cone_side_classifier import (
    classify_cone_sides,
    LEFT,
    RIGHT,
    StableSideLabels,
    vehicle_relative_side_vote,
)
from lhr_perception.pose_history import Pose2D
from lhr_track_builder.centerline import pair_classified_cones
from lhr_trackgen.publish_cones import generate_autocross_track
import pytest


def _spawn_index(centerline):
    """Match the straight-section selection used by the world generator."""
    count = len(centerline)
    margin = max(8, count // 10)

    def curvature(index):
        previous = centerline[index - 5]
        current = centerline[index]
        following = centerline[index + 5]
        before = math.atan2(
            current[1] - previous[1], current[0] - previous[0])
        after = math.atan2(
            following[1] - current[1], following[0] - current[0])
        return abs(math.atan2(
            math.sin(after - before), math.cos(after - before)))

    return min(range(margin, count - margin), key=curvature)


def _one_metre_trajectory(centerline, start):
    """Interpolate the cone-spaced centerline into independent viewpoints."""
    trajectory = []
    for offset in range(len(centerline)):
        first = centerline[(start + offset) % len(centerline)]
        second = centerline[(start + offset + 1) % len(centerline)]
        steps = max(1, round(math.dist(first, second)))
        yaw = math.atan2(second[1] - first[1], second[0] - first[0])
        for step in range(steps):
            fraction = step / steps
            trajectory.append((
                first[0] + (second[0] - first[0]) * fraction,
                first[1] + (second[1] - first[1]) * fraction,
                yaw,
            ))
    return trajectory


@pytest.mark.parametrize('seed', [1, 8, 10, 11, 20, 30])
def test_progressive_map_has_stable_sides_and_unique_midpoints(seed):
    """Keep partial-map topology from locking a wrong autocross boundary."""
    left, right = generate_autocross_track(seed=seed)
    cones = left + right
    truth = [LEFT] * len(left) + [RIGHT] * len(right)
    centerline = [
        ((left_point[0] + right_point[0]) / 2.0,
         (left_point[1] + right_point[1]) / 2.0)
        for left_point, right_point in zip(left, right)
    ]
    trajectory = _one_metre_trajectory(
        centerline, _spawn_index(centerline))
    mapped_indices = []
    mapped_set = set()
    votes = []
    stable = StableSideLabels(confirmations=2)
    rng = random.Random(1000 + seed)
    labels = []

    for step, (vehicle_x, vehicle_y, yaw) in enumerate(trajectory):
        lateral_error = rng.gauss(0.0, 0.5)
        vehicle_x -= math.sin(yaw) * lateral_error
        vehicle_y += math.cos(yaw) * lateral_error
        yaw += math.radians(rng.gauss(0.0, 5.0))
        pose = Pose2D(step, vehicle_x, vehicle_y, yaw)

        for index, cone in enumerate(cones):
            if index in mapped_set:
                continue
            if 0.8 <= math.dist((vehicle_x, vehicle_y), cone) <= 20.0:
                mapped_set.add(index)
                mapped_indices.append(index)
                votes.append(0.0)

        for local_index, cone_index in enumerate(mapped_indices):
            votes[local_index] += vehicle_relative_side_vote(
                *cones[cone_index], pose)

        proposed = classify_cone_sides(
            [cones[index] for index in mapped_indices], votes)
        labels = stable.update(proposed, seed=step == 0)
        assert all(
            label == 0 or label == truth[mapped_indices[index]]
            for index, label in enumerate(labels)
        )

    assert labels == [truth[index] for index in mapped_indices]
    inferred_left = [
        cones[mapped_indices[index]] for index, label in enumerate(labels)
        if label == LEFT
    ]
    inferred_right = [
        cones[mapped_indices[index]] for index, label in enumerate(labels)
        if label == RIGHT
    ]
    midpoints = pair_classified_cones(
        inferred_left, inferred_right, track_width=3.5, width_tolerance=1.0)

    assert len(midpoints) == len(centerline)
    for midpoint in midpoints:
        error = min(math.dist(midpoint, expected) for expected in centerline)
        assert error < 0.25
