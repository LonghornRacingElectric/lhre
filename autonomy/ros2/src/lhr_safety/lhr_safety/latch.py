"""
What safety_node does with a fault: arm, latch, heartbeat, reset.

Free of ROS imports, like monitors.py, so the state machine is unit tested
directly and the node only publishes what step() decides.
"""

from dataclasses import dataclass
from typing import Optional

from lhr_safety.monitors import Fault

# A fault only trips the car in these states. Before READY the stack is
# still coming up; after FINISHED or EMERGENCY it is already stopped.
ARMED_STATES = ('READY', 'DRIVING')


@dataclass(frozen=True)
class Decision:
    """One tick's outputs."""

    emergency: bool
    heartbeat: Optional[int]
    shown: Fault
    newly_tripped: bool
    newly_armed: bool


class SafetyLatch:
    """Arms on the mission state, latches the first fault until reset."""

    def __init__(self):
        self.tripped = Fault.NONE
        self.counter = 0
        self._armed = False

    def step(self, fault: Fault, mission_state: str) -> Decision:
        """Decide this tick's emergency, heartbeat and status."""
        armed = mission_state in ARMED_STATES
        newly_armed = armed and not self._armed
        self._armed = armed

        newly_tripped = (armed and fault != Fault.NONE
                         and self.tripped == Fault.NONE)
        if newly_tripped:
            self.tripped = fault

        heartbeat = None
        if self.tripped == Fault.NONE and fault == Fault.NONE:
            # The heartbeat means "the software says it is safe to drive".
            # A fault stops it even when not armed, and a latched trip keeps
            # it stopped after the fault itself has gone.
            self.counter += 1
            heartbeat = self.counter

        return Decision(
            # Every tick while latched, not once: a single dropped message
            # must not leave the car driving.
            emergency=self.tripped != Fault.NONE,
            heartbeat=heartbeat,
            shown=self.tripped if self.tripped != Fault.NONE else fault,
            newly_tripped=newly_tripped,
            newly_armed=newly_armed,
        )

    def reset(self) -> Fault:
        """Clear the latch; return what was latched (NONE if nothing)."""
        cleared, self.tripped = self.tripped, Fault.NONE
        return cleared
