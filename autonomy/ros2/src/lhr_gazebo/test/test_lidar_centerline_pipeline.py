"""Regression tests for progressive LiDAR autocross perception."""

import math
import random
from types import SimpleNamespace

from lhr_perception.cone_side_classifier import (
    classify_cone_sides,
    LEFT,
    RIGHT,
    StableSideLabels,
    vehicle_relative_side_vote,
)
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_perception.pose_history import Pose2D
from lhr_track_builder.centerline import pair_classified_cones
from lhr_track_builder.track_builder_node import TrackBuilder
from lhr_trackgen.publish_cones import generate_autocross_track
import pytest
import rclpy


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
        labels = stable.update(proposed)
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


@pytest.mark.parametrize('seed', [1, 2, 3, 4, 5])
def test_sparse_viewpoints_recover_boundaries_and_centerline(seed):
    """Replay partial maps through the real detector rather than seeding full visibility."""
    left, right = generate_autocross_track(seed=seed, width_m=3.)
    cones = left + right
    truth = [LEFT] * len(left) + [RIGHT] * len(right)
    centerline = [((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(left, right)]
    trajectory = _one_metre_trajectory(centerline, _spawn_index(centerline))
    rclpy.init()
    detector = LidarConeDetector()
    rng = random.Random(seed)
    mapped = []
    seen = set()
    try:
        # Revisit the course so sparse late detections have independent viewpoints.
        for step, (x, y, yaw) in enumerate(trajectory + trajectory):
            for index, cone in enumerate(cones):
                if index not in seen and math.dist((x, y), cone) < 15. and rng.random() < .25:
                    mapped.append(index)
                    seen.add(index)
                    detector._cone_map.append([*cone, 1.])
            detector._update_side_classification(Pose2D(step+1, x, y, yaw))
        assert len(seen) == len(cones)
        assert detector._cone_sides == [truth[index] for index in mapped]
        inferred_left = [cones[i] for i, side in zip(mapped, detector._cone_sides)
                         if side == LEFT]
        inferred_right = [cones[i] for i, side in zip(mapped, detector._cone_sides)
                          if side == RIGHT]
        midpoints = pair_classified_cones(inferred_left, inferred_right, 3., 1.)
        assert len(midpoints) == len(centerline)
        assert max(min(math.dist(p, expected) for expected in centerline) for p in midpoints) < .25
    finally:
        detector.destroy_node()
        rclpy.shutdown()


def test_unknown_startup_and_corrected_labels_reach_geometry_planner():
    """Unknown sides can start a path, and corrected snapshots replace old planner IDs."""
    rclpy.init(args=['--ros-args', '-p', 'pairing_strategy:=boundary',
                     '-p', 'boundary_use_color:=false'])
    detector, planner = LidarConeDetector(), TrackBuilder()
    detector._cone_map = [[x, y, 1.] for y in (1.5, -1.5) for x in (4., 6., 8.)]
    detector._det_pub = SimpleNamespace(publish=planner._cones_cb)
    try:
        detector._update_side_classification(Pose2D(1, 0., 3., 0.))
        detector._publish_accumulated()
        assert len(planner._unknown_cones) == 6
        assert planner._compute_midpoints()
        for stamp, x in enumerate((1., 2.), start=2):
            detector._update_side_classification(Pose2D(stamp, x, 3., 0.))
        detector._publish_accumulated()
        assert 0 in planner._right_cones
        assert planner._compute_midpoints(), (
            'Wrong inferred sides must not prevent recovery motion')
        for stamp, x in enumerate((3., 4., 5., 6.), start=4):
            detector._update_side_classification(Pose2D(stamp, x, 0., 0.))
        detector._publish_accumulated()
        assert set(planner._left_cones) == {0, 1, 2}
        assert set(planner._right_cones) == {3, 4, 5}
        path = planner._compute_midpoints()
        assert path
        assert max(abs(y) for _, y in path) < 1e-6
    finally:
        planner.destroy_node()
        detector.destroy_node()
        rclpy.shutdown()
