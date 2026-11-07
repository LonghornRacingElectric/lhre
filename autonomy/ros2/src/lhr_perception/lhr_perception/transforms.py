"""Shared full-pose rigid transforms for LiDAR registration and camera fusion."""

import numpy as np


def rigid_transform_matrix(transform):
    """Convert a geometry Transform to a finite homogeneous matrix."""
    q = transform.rotation
    quat = np.array([q.x, q.y, q.z, q.w], dtype=np.float64)
    norm = np.linalg.norm(quat)
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError('Transform quaternion must be finite and nonzero')
    x, y, z, w = quat / norm
    matrix = np.eye(4)
    matrix[:3, :3] = [
        [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
        [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
        [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)],
    ]
    t = transform.translation
    matrix[:3, 3] = [t.x, t.y, t.z]
    if not np.isfinite(matrix).all():
        raise ValueError('Transform translation must be finite')
    return matrix
