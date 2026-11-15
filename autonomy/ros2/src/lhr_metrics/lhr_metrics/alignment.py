"""Pair each estimate with truth at the same stamp, so delivery delay is not scored as error."""

from collections import deque
import math
from typing import List, Tuple


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


class TruthAligner:
    """
    Buffer stamped truth and score each estimate against truth interpolated at its stamp.

    Comparing with the latest received truth sample instead scored scheduling: a
    0.1 s stall at 5 m/s reads as 0.5 m of estimator error (#165). An estimate
    waits until truth at or past its stamp has arrived. One older than the
    buffer is skipped, never compared with a stale sample.
    """

    def __init__(self, horizon_s: float = 2.0):
        self._horizon = horizon_s
        self._truth = deque()    # (stamp, x, y, yaw), increasing stamps
        self._pending = deque()  # estimates waiting for truth that covers them

    def add_truth(self, stamp, x, y, yaw) -> List[Tuple[float, float]]:
        """Buffer one truth sample; return (position, yaw) errors it lets us score."""
        if self._truth and stamp <= self._truth[-1][0]:
            return []  # repeated or out-of-order stamps cannot bracket anything new
        self._truth.append((stamp, x, y, yaw))
        while self._truth[0][0] < stamp - self._horizon:
            self._truth.popleft()
        return self._drain()

    def add_estimate(self, stamp, x, y, yaw) -> List[Tuple[float, float]]:
        """Queue one estimate; return the errors now scoreable, possibly including it."""
        self._pending.append((stamp, x, y, yaw))
        # Without truth arriving, keep only a horizon's worth of estimates.
        while self._pending[0][0] < stamp - self._horizon:
            self._pending.popleft()
        return self._drain()

    def _drain(self) -> List[Tuple[float, float]]:
        errors = []
        while self._pending and self._truth and self._pending[0][0] <= self._truth[-1][0]:
            stamp, x, y, yaw = self._pending.popleft()
            if stamp < self._truth[0][0]:
                continue  # before the buffered truth: no honest comparison
            tx, ty, tyaw = self._interpolate(stamp)
            errors.append((math.hypot(x - tx, y - ty), abs(_wrap(yaw - tyaw))))
        return errors

    def _interpolate(self, stamp):
        later = next(i for i, sample in enumerate(self._truth) if sample[0] >= stamp)
        t1, x1, y1, yaw1 = self._truth[later]
        if later == 0 or t1 == stamp:
            return x1, y1, yaw1
        t0, x0, y0, yaw0 = self._truth[later - 1]
        f = (stamp - t0) / (t1 - t0)
        return (x0 + f*(x1 - x0), y0 + f*(y1 - y0), yaw0 + f*_wrap(yaw1 - yaw0))
