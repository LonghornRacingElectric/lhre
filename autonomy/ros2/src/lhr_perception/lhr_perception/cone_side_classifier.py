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
    """Confirm side labels and require stronger persistence to reverse them."""

    def __init__(self, confirmations: int = 2):
        if confirmations < 1:
            raise ValueError('confirmations must be positive')
        self._confirmations = confirmations
        self._labels: list[int] = []
        self._pending: list[int] = []
        self._counts: list[int] = []

    def labels(self, size: int) -> list[int]:
        """Resize for newly mapped cones and return the accepted labels."""
        if size < len(self._labels):
            raise ValueError('cone map cannot shrink')
        extension = size - len(self._labels)
        self._labels.extend([UNKNOWN] * extension)
        self._pending.extend([UNKNOWN] * extension)
        self._counts.extend([0] * extension)
        return list(self._labels)

    def update(self, proposed: Sequence[int]) -> list[int]:
        """Incorporate one independently observed set of proposed labels."""
        self.labels(len(proposed))
        for index, side in enumerate(proposed):
            if side == UNKNOWN or side == self._labels[index]:
                self._pending[index] = UNKNOWN
                self._counts[index] = 0
                continue
            if side == self._pending[index]:
                self._counts[index] += 1
            else:
                self._pending[index] = side
                self._counts[index] = 1
            required = self._confirmations * (1 if self._labels[index] == UNKNOWN else 2)
            if self._counts[index] >= required:
                self._labels[index] = side
                self._pending[index] = UNKNOWN
                self._counts[index] = 0
        return list(self._labels)


class _DisjointSet:
    """Track connected boundary fragments."""

    def __init__(self, votes: Sequence[float]):
        size = len(votes)
        self._parents = list(range(size))
        self._sizes = [1] * size
        self._votes = list(votes)

    def find(self, item: int) -> int:
        """Return the representative for one item."""
        while self._parents[item] != item:
            self._parents[item] = self._parents[self._parents[item]]
            item = self._parents[item]
        return item

    def union(self, first: int, second: int) -> bool:
        """Join fragments unless both have opposing side evidence."""
        first = self.find(first)
        second = self.find(second)
        if first == second:
            return True
        # A nominal 7m observation weighs about 0.12; ignore weaker noise,
        # but never let a short edge erase independently observed sides.
        if (self._votes[first] * self._votes[second] < 0
                and min(abs(self._votes[first]), abs(self._votes[second])) >= 0.1):
            return False
        if self._sizes[first] < self._sizes[second]:
            first, second = second, first
        self._parents[second] = first
        self._sizes[first] += self._sizes[second]
        self._votes[first] += self._votes[second]
        return True


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
    fresh_votes: Sequence[float] | None = None,
) -> list[int]:
    """Share boundary votes, proposing sides only on currently observed fragments."""
    if len(cone_positions) != len(side_votes):
        raise ValueError('cone_positions and side_votes must have equal length')
    if fresh_votes is not None and len(fresh_votes) != len(side_votes):
        raise ValueError('fresh_votes and side_votes must have equal length')
    if not cone_positions:
        return []

    points = np.asarray(
        [(float(point[0]), float(point[1])) for point in cone_positions])
    unlinked = [_sign(vote) if fresh_votes is None or fresh_votes[index] else UNKNOWN
                for index, vote in enumerate(side_votes)]
    if len(points) < 3:
        return unlinked

    try:
        triangulation = Delaunay(points)
    except QhullError:
        return unlinked

    edges: set[tuple[int, int]] = set()
    for simplex in triangulation.simplices:
        for offset in range(3):
            first = int(simplex[offset])
            second = int(simplex[(offset + 1) % 3])
            edges.add((min(first, second), max(first, second)))

    components = _DisjointSet(side_votes)
    neighbors: list[list[int]] = [[] for _ in points]
    edge_lengths = {edge: float(np.linalg.norm(points[edge[0]] - points[edge[1]]))
                    for edge in edges}
    # Grow nearby fragments before considering wider, potentially cross-track links.
    ordered_edges = sorted(edges, key=lambda edge: (edge_lengths[edge], edge))
    for first, second in ordered_edges:
        distance = edge_lengths[(first, second)]
        if distance <= link_distance and components.union(first, second):
            neighbors[first].append(second)
            neighbors[second].append(first)

    tangents = [
        _local_tangent(index, adjacent, points)
        for index, adjacent in enumerate(neighbors)
    ]
    minimum_alignment = math.cos(max_gap_angle)
    for first, second in ordered_edges:
        distance = edge_lengths[(first, second)]
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

    observed = {components.find(index) for index in range(len(points))
                if fresh_votes is None or fresh_votes[index]}
    return [
        _sign(component_votes[components.find(index)])
        if components.find(index) in observed else UNKNOWN
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
