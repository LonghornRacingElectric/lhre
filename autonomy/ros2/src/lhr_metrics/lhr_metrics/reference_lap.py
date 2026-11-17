"""
The reference lap: the fastest standing-start lap of the reference centerline.

Sim v2 scores lap time against this instead of a truth-stack run (#232).
It is the time to drive the world's reference centerline once around, from
rest at the start/finish gate, under the speed limits pursuit_node drives
under: v_max, the lateral-acceleration limit a_lat_max (with v_min as its
floor, as the planner has), max_accel and max_decel. It depends only on the
course and those numbers, so every machine gets the same reference.
"""

import math

from lhr_metrics.reference import ReferenceTrack

STEP_M = 0.5  # arc-length sampling of the centerline
# Curvature comes from the circle through the points this far either side
# of each sample, so cone-placement jitter between vertices does not read
# as a sharp corner. On a true circle any chord gives the exact radius.
CHORD_M = 3.0


def _point_at(track, s):
    """Return the centerline point at arc length s (wrapped on the loop)."""
    s %= track.length
    for (ax, ay), dx, dy, length, arc in track.segments:
        if s <= arc + length:
            fraction = (s - arc) / length
            return ax + fraction * dx, ay + fraction * dy
    (ax, ay), dx, dy, _, _ = track.segments[-1]
    return ax + dx, ay + dy


def _curvature(a, b, c):
    """Unsigned curvature of the circle through three points (0 if collinear)."""
    ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    denom = ab * bc * ca
    return 0.0 if denom < 1e-12 else abs(2.0 * cross / denom)


def reference_lap(points, *, start, tangent, v_max, v_min, a_lat_max,
                  max_accel, max_decel):
    """
    Return the reference lap time in seconds.

    points is the closed reference centerline. start and tangent are the
    start/finish gate's position and driving direction, which pick where
    the lap starts from rest and which way it goes round.
    """
    limits = (v_max, a_lat_max, max_accel, max_decel)
    if not all(math.isfinite(x) and x > 0.0 for x in limits) or not 0.0 <= v_min <= v_max:
        raise ValueError('speed limits must be positive, with 0 <= v_min <= v_max')
    track = ReferenceTrack(points, closed=True)
    _, s0, _, (tx, ty) = track.project(*start)
    direction = 1.0 if tx * tangent[0] + ty * tangent[1] >= 0.0 else -1.0

    n = max(3, math.ceil(track.length / STEP_M))
    step = track.length / n
    # A chord longer than a sixth of the loop would wrap round it.
    chord = min(CHORD_M, track.length / 6.0)
    arcs = [s0 + direction * i * step for i in range(n + 1)]
    speed = []
    for s in arcs:
        kappa = _curvature(_point_at(track, s - chord), _point_at(track, s),
                           _point_at(track, s + chord))
        corner = math.sqrt(a_lat_max / kappa) if kappa > 0.0 else math.inf
        speed.append(max(v_min, min(v_max, corner)))

    # From rest at the gate; the finish is flying, so only the corner limits
    # bound the last sample.
    speed[0] = 0.0
    for i in range(1, n + 1):
        speed[i] = min(speed[i], math.sqrt(speed[i - 1] ** 2 + 2.0 * max_accel * step))
    for i in range(n - 1, 0, -1):
        speed[i] = min(speed[i], math.sqrt(speed[i + 1] ** 2 + 2.0 * max_decel * step))
    # Constant acceleration across each step.
    return sum(2.0 * step / (a + b) for a, b in zip(speed, speed[1:]))
