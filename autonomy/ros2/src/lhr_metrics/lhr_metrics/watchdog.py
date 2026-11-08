"""
The metrics run watchdog: a sim-time deadline with a wall-time hang guard.

The deadline counts the node's clock, which is sim time under
use_sim_time, so a slow machine only makes the run take longer (#227). Wall
time catches a sim that died: if the clock stands still for as many wall
seconds as the deadline, including never starting, the run is hung.
"""

from typing import Optional


class RunWatchdog:
    """Decide whether a run is over from its clock and the wall clock."""

    def __init__(self, timeout_sec: float, wall_now: float):
        self.timeout_sec = timeout_sec
        self._start: Optional[float] = None
        self._last: Optional[float] = None
        self._moved = wall_now

    def check(self, clock_now: float, wall_now: float) -> Optional[str]:
        """
        Return 'timeout', 'hung' or None.

        clock_now is the node clock in seconds; zero means sim time has not
        started. wall_now is a monotonic wall clock in seconds.
        """
        if self.timeout_sec <= 0.0:
            return None
        if clock_now > 0.0 and (self._last is None or clock_now > self._last):
            self._moved = wall_now
            if self._start is None:
                self._start = clock_now
        self._last = clock_now
        if self._start is not None and clock_now - self._start >= self.timeout_sec:
            return 'timeout'
        if wall_now - self._moved >= self.timeout_sec:
            return 'hung'
        return None
