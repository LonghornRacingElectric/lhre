"""Communication-state decisions without Kafka or wall-clock dependencies."""

import math

BOARDS = ("csm", "dui", "hvc", "inverter", "pdu", "tsm", "usm", "vcu")


def classify(last_seen_s, stale_after_s):
    if isinstance(last_seen_s, bool) or not isinstance(last_seen_s, (int, float)):
        return "UNKNOWN"
    if not math.isfinite(last_seen_s):
        return "UNKNOWN"
    if last_seen_s == -1:
        return "NEVER_SEEN"
    if last_seen_s < 0:
        return "UNKNOWN"
    return "STALE" if last_seen_s >= stale_after_s else "LIVE"


class BoardStateTracker:
    def __init__(self, stale_after_s, telemetry_timeout_s):
        for value in (stale_after_s, telemetry_timeout_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite, positive seconds")
        self.stale_after_s = stale_after_s
        self.telemetry_timeout_s = telemetry_timeout_s
        self.states = {board: "UNKNOWN" for board in BOARDS}
        self.last_frame_at = None

    def _set_states(self, states):
        changes = {
            board: (self.states[board], state)
            for board, state in states.items()
            if self.states[board] != state
        }
        self.states.update(states)
        return changes

    def observe(self, board_status, now):
        self.last_frame_at = now
        if not isinstance(board_status, dict):
            board_status = {}
        return self._set_states({
            board: classify(board_status.get(f"{board}_last_seen_s"), self.stale_after_s)
            for board in BOARDS
        })

    def expire(self, now):
        # Silence produces no records, so the poll loop must also call this when empty.
        if self.last_frame_at is None or now - self.last_frame_at < self.telemetry_timeout_s:
            return {}
        return self._set_states({board: "UNKNOWN" for board in BOARDS})


def _finite_or_none(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def change_events(changes, board_status, cause, t_ms, car="orion"):
    """One JSON-ready event per changed board, in board order.

    Timeout changes have no frame behind them, so their last_seen_s is null.
    Invalid values become null too: MessageToDict renders NaN/inf as strings.
    """
    if cause == "telemetry_timeout" or not isinstance(board_status, dict):
        board_status = {}
    return [
        {
            "car": car,
            "board": board,
            "state": current,
            "previous": previous,
            "last_seen_s": _finite_or_none(board_status.get(f"{board}_last_seen_s")),
            "cause": cause,
            "t_ms": t_ms,
        }
        for board, (previous, current) in sorted(changes.items())
    ]
