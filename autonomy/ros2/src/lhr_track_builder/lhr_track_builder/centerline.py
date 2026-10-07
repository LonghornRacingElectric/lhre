"""Pure centerline construction helpers."""

from typing import Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment


def pair_classified_cones(
    left_cones: Sequence[Sequence[float]],
    right_cones: Sequence[Sequence[float]],
    track_width: float,
    width_tolerance: float,
) -> list[tuple[float, float]]:
    """Return a minimum-cost one-to-one pairing inside the width gate."""
    if not left_cones or not right_cones:
        return []

    left = np.asarray(left_cones, dtype=float)
    right = np.asarray(right_cones, dtype=float)
    distances = np.linalg.norm(left[:, None, :] - right[None, :, :], axis=2)
    minimum_width = track_width - width_tolerance
    maximum_width = track_width + width_tolerance

    left_count = len(left)
    right_count = len(right)
    size = left_count + right_count
    forbidden_cost = 1_000_000.0
    unmatched_cost = maximum_width
    costs = np.full((size, size), forbidden_cost)

    valid = (distances >= minimum_width) & (distances <= maximum_width)
    costs[:left_count, :right_count][valid] = distances[valid]
    costs[:left_count, right_count:] = unmatched_cost
    costs[left_count:, :right_count] = unmatched_cost
    costs[left_count:, right_count:] = 0.0

    rows, columns = linear_sum_assignment(costs)
    midpoints = []
    for row, column in zip(rows, columns):
        if row >= left_count or column >= right_count:
            continue
        if not valid[row, column]:
            continue
        midpoint = (left[row] + right[column]) / 2.0
        midpoints.append((float(midpoint[0]), float(midpoint[1])))
    return midpoints
