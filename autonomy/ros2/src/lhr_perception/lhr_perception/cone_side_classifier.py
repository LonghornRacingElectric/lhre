"""Infer left and right track boundaries from vehicle-relative cone positions."""

import math
from typing import Sequence

from lhr_perception.pose_history import Pose2D
import numpy as np
from scipy.spatial import Delaunay, QhullError


UNKNOWN = 0
LEFT = 1
RIGHT = -1


class StableSideLabels:
    """Lock side labels after consistent, independently spaced proposals."""

    def __init__(self, confirmations: int = 2):
        if confirmations < 1:
            raise ValueError('confirmations must be positive')
        self._confirmations = confirmations
        self._labels: list[int] = []
        self._pending: list[int] = []
        self._counts: list[int] = []

    def labels(self, size: int) -> list[int]:
        """Resize for newly mapped cones and return the locked labels."""
        if size < len(self._labels):
            raise ValueError('cone map cannot shrink')
        extension = size - len(self._labels)
        self._labels.extend([UNKNOWN] * extension)
        self._pending.extend([UNKNOWN] * extension)
        self._counts.extend([0] * extension)
        return list(self._labels)

    def update(self, proposed: Sequence[int], seed: bool = False) -> list[int]:
        """Incorporate one independently observed set of proposed labels."""
        self.labels(len(proposed))
        for index, side in enumerate(proposed):
            if self._labels[index] != UNKNOWN or side == UNKNOWN:
                continue
            if seed:
                self._labels[index] = side
                continue
            if side == self._pending[index]:
                self._counts[index] += 1
            else:
                self._pending[index] = side
                self._counts[index] = 1
            if self._counts[index] >= self._confirmations:
                self._labels[index] = side
        return list(self._labels)


class _DisjointSet:
    """Track connected boundary fragments."""

    def __init__(self, size: int):
        self._parents = list(range(size))
        self._sizes = [1] * size

    def find(self, item: int) -> int:
        """Return the representative for one item."""
        while self._parents[item] != item:
            self._parents[item] = self._parents[self._parents[item]]
            item = self._parents[item]
        return item

    def union(self, first: int, second: int):
        """Join two components."""
        first = self.find(first)
        second = self.find(second)
        if first == second:
            return
        if self._sizes[first] < self._sizes[second]:
            first, second = second, first
        self._parents[second] = first
        self._sizes[first] += self._sizes[second]


def vehicle_relative_side_vote(
    cone_x: float,
    cone_y: float,
    pose: Pose2D,
    min_distance: float = 0.8,
    max_distance: float = 7.0,
    max_forward: float = 3.0,
    max_lateral: float = 5.0,
    lateral_deadband: float = 0.5,
) -> float:
    """Return a weighted side observation for a cone in the local corridor."""
    dx = cone_x - pose.x
    dy = cone_y - pose.y
    cosine = math.cos(pose.yaw)
    sine = math.sin(pose.yaw)
    forward = cosine * dx + sine * dy
    lateral = -sine * dx + cosine * dy
    distance = math.hypot(dx, dy)

    if distance < min_distance or distance > max_distance:
        return 0.0
    if forward < 0.0 or forward > max_forward:
        return 0.0
    if abs(lateral) < lateral_deadband or abs(lateral) > max_lateral:
        return 0.0

    side = LEFT if lateral > 0.0 else RIGHT
    return side / (1.0 + 0.15 * distance * distance)


def classify_cone_sides(
    cone_positions: Sequence[Sequence[float]],
    side_votes: Sequence[float],
    link_distance: float = 3.0,
    gap_distance: float = 3.3,
    max_gap_angle: float = math.radians(40.0),
) -> list[int]:
    """Classify cones after sharing votes along geometric boundary fragments."""
    if len(cone_positions) != len(side_votes):
        raise ValueError('cone_positions and side_votes must have equal length')
    if not cone_positions:
        return []

    points = np.asarray(
        [(float(point[0]), float(point[1])) for point in cone_positions])
    if len(points) < 3:
        return [_sign(vote) for vote in side_votes]

    try:
        triangulation = Delaunay(points)
    except QhullError:
        return [_sign(vote) for vote in side_votes]

    edges: set[tuple[int, int]] = set()
    for simplex in triangulation.simplices:
        for offset in range(3):
            first = int(simplex[offset])
            second = int(simplex[(offset + 1) % 3])
            edges.add((min(first, second), max(first, second)))

    components = _DisjointSet(len(points))
    neighbors: list[list[int]] = [[] for _ in points]
    edge_lengths: dict[tuple[int, int], float] = {}
    for first, second in edges:
        distance = float(np.linalg.norm(points[first] - points[second]))
        edge_lengths[(first, second)] = distance
        if distance <= link_distance:
            components.union(first, second)
            neighbors[first].append(second)
            neighbors[second].append(first)

    tangents = [
        _local_tangent(index, adjacent, points)
        for index, adjacent in enumerate(neighbors)
    ]
    minimum_alignment = math.cos(max_gap_angle)
    for (first, second), distance in edge_lengths.items():
        if distance <= link_distance or distance > gap_distance:
            continue
        if components.find(first) == components.find(second):
            continue

        edge_direction = (points[second] - points[first]) / distance
        alignments = []
        if tangents[first] is not None:
            alignments.append(abs(float(edge_direction @ tangents[first])))
        if tangents[second] is not None:
            alignments.append(abs(float(edge_direction @ tangents[second])))
        if alignments and min(alignments) >= minimum_alignment:
            components.union(first, second)

    component_votes: dict[int, float] = {}
    for index, vote in enumerate(side_votes):
        root = components.find(index)
        component_votes[root] = component_votes.get(root, 0.0) + vote

    return [
        _sign(component_votes[components.find(index)])
        for index in range(len(points))
    ]


def _local_tangent(
    index: int,
    neighbors: Sequence[int],
    points: np.ndarray,
) -> np.ndarray | None:
    """Estimate the boundary direction around one cone."""
    if not neighbors:
        return None
    vectors = np.asarray([points[neighbor] - points[index]
                          for neighbor in neighbors])
    covariance = vectors.T @ vectors
    _, eigenvectors = np.linalg.eigh(covariance)
    return eigenvectors[:, -1]


def _sign(value: float) -> int:
    """Convert a vote sum to a side label."""
    if value > 0.0:
        return LEFT
    if value < 0.0:
        return RIGHT
    return UNKNOWN
