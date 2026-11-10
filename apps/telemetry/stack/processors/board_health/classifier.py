"""Communication-state decisions without Kafka or wall-clock dependencies."""

import math

# Earliest frame time accepted as a real clock reading (2025-01-01 UTC). The
# car's CM5 sets its clock over the network; booting without a network or an
# RTC backup battery can leave it at 1970 or another wrong date until it syncs.
MIN_VALID_FRAME_TIME_MS = 1_735_689_600_000
# A frame stamped further than this ahead of the server clock is treated as a
# wrong car clock. Frames behind the server clock are kept: replayed backlog is
# legitimately old.
MAX_FUTURE_SKEW_MS = 5 * 60 * 1000


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
    def __init__(self, stale_after_s, telemetry_timeout_s, board_fields):
        for value in (stale_after_s, telemetry_timeout_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite, positive seconds")
        self.stale_after_s = stale_after_s
        self.telemetry_timeout_s = telemetry_timeout_s
        self.board_fields = dict(board_fields)
        self.states = {board: "UNKNOWN" for board in self.board_fields}
        self.last_observed_at = {}

    def _set_states(self, states):
        changes = {
            board: (self.states[board], state)
            for board, state in states.items()
            if self.states[board] != state
        }
        self.states.update(states)
        return changes

    def observe(self, board_status, now):
        if not isinstance(board_status, dict):
            return {}
        states = {}
        for board, field in self.board_fields.items():
            if field not in board_status:
                continue
            self.last_observed_at[board] = now
            states[board] = classify(board_status[field], self.stale_after_s)
        return self._set_states(states)

    def expire(self, now):
        # Only actual health observations refresh a board's arrival timeout.
        return self._set_states({
            board: "UNKNOWN" for board, observed in self.last_observed_at.items()
            if now - observed >= self.telemetry_timeout_s
        })


def _finite_or_none(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _int_or_none(value):
    # MessageToDict renders int64 fields such as `time` as decimal strings.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def event_time_ms(frame_time, server_now_ms):
    """Pick an event timestamp: (t_ms, "car") or (t_ms, "server").

    BEVO's observation time is retained when updates arrive late or are replayed. It is used when it looks like a
    synced clock; otherwise the server's time is used and labelled as such.
    """
    car_ms = _int_or_none(frame_time)
    if (car_ms is not None
            and MIN_VALID_FRAME_TIME_MS <= car_ms <= server_now_ms + MAX_FUTURE_SKEW_MS):
        return car_ms, "car"
    return server_now_ms, "server"


def change_events(changes, board_status, cause, t_ms, t_source="server", car="artemis"):
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
            "t_source": t_source,
        }
        for board, (previous, current) in sorted(changes.items())
    ]
