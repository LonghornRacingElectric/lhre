#!/usr/bin/env python3
"""Arc-length-weighted cross-track error. No ROS dependency, so testable."""

import math
from typing import Optional, Tuple


class TrackErrorAccumulator:
    """
    Accumulate cross-track error weighted by distance travelled.

    Averaging per sample makes the result depend on the speed profile
    and the odom publish rate: a slow corner yields more samples per
    metre than a fast straight, so the mean drifts toward wherever the
    car was slowest, and two runs whose samples landed differently
    disagree on a lap they drove the same way. Integrating over arc
    length instead makes the mean a property of the path driven, which
    is the thing a regression gate is trying to compare.
    """

    def __init__(self, off_track_threshold: float):
        self._threshold = off_track_threshold
        self._prev: Optional[Tuple[float, float, float]] = None
        self._integral = 0.0
        self.samples = 0
        self.max_cte = 0.0
        self.off_track_samples = 0
        self.path_length = 0.0
        self.off_track_distance = 0.0

    def add(self, x: float, y: float, cte: float) -> None:
        """Record one pose and its distance from the centerline."""
        self.samples += 1
        if cte > self.max_cte:
            self.max_cte = cte
        if cte > self._threshold:
            self.off_track_samples += 1

        if self._prev is not None:
            px, py, prev_cte = self._prev
            ds = math.hypot(x - px, y - py)
            # Trapezoid over the segment: its mean stands in for the
            # whole segment better than either endpoint does, and it
            # keeps the total steady when samples land in slightly
            # different places between runs.
            segment_cte = 0.5 * (prev_cte + cte)
            self._integral += segment_cte * ds
            self.path_length += ds
            if segment_cte > self._threshold:
                self.off_track_distance += ds

        self._prev = (x, y, cte)

    @property
    def mean_cte(self) -> float:
        """Return the arc-length-weighted mean cross-track error."""
        if self.path_length > 0.0:
            return self._integral / self.path_length
        # A car that never moved has no arc length to average over.
        # Every sample sits in one place, so the max is that value;
        # returning 0.0 would read as "sitting on the centerline".
        return self.max_cte
