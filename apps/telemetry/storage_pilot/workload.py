"""Deterministic observations and a separate cached-snapshot baseline."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import heapq
import math

SCHEMA_ID = "dummy-v1"


@dataclass(frozen=True)
class Signal:
    signal_id: int
    name: str
    unit: str
    value_type: str
    hz: int


def signals(speed_hz=100, temperature_hz=1, voltage_hz=10):
    rates = (speed_hz, temperature_hz, voltage_hz)
    if any(not isinstance(rate, int) or not 1 <= rate <= 1000 for rate in rates):
        raise ValueError("Signal rates must be integers between 1 and 1000 Hz")
    return (
        Signal(1, "motor_speed", "rpm", "int", speed_hz),
        Signal(2, "ambient_temperature", "°C", "float", temperature_hz),
        Signal(3, "pack_voltage", "V", "float", voltage_hz),
    )


@dataclass(frozen=True)
class Observation:
    session_id: str
    schema_id: str
    signal_id: int
    sample_id: int
    ts: datetime
    value_type: str
    value: int | float
    quality: str = "valid"


def value_for(signal_id, sample_id, seed):
    phase = (seed % 360) * math.pi / 180
    if signal_id == 1:
        # Ten observations share a reading; identity comes from the sample,
        # never from a comparison with the previous value.
        return round(3200 + 600 * math.sin((sample_id // 10) / 20 + phase))
    if signal_id == 2:
        return round(42 + 2 * math.sin(sample_id / 30 + phase), 3)
    return round(400 + 5 * math.sin(sample_id / 40 + phase), 3)


def observations(session_id, start, seconds, definitions, seed=1):
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("Start time must include a timezone")
    if not isinstance(seconds, int) or seconds <= 0:
        raise ValueError("Duration must be a positive integer number of seconds")
    definitions = tuple(definitions)
    by_id = {signal.signal_id: signal for signal in definitions}
    if len(by_id) != len(definitions) or not definitions:
        raise ValueError("Signal definitions must be nonempty and unique")
    pending = [(0, signal.signal_id, 0) for signal in definitions]
    heapq.heapify(pending)
    while pending:
        elapsed_us, signal_id, sample_id = heapq.heappop(pending)
        if elapsed_us >= seconds * 1_000_000:
            break
        signal = by_id[signal_id]
        yield Observation(
            str(session_id), SCHEMA_ID, signal_id, sample_id,
            start.astimezone(timezone.utc) + timedelta(microseconds=elapsed_us),
            signal.value_type, value_for(signal_id, sample_id, seed),
        )
        next_id = sample_id + 1
        heapq.heappush(pending, (next_id * 1_000_000 // signal.hz, signal_id, next_id))


def snapshots(session_id, start, seconds, definitions, seed=1):
    """Sample the same source timeline at its fastest rate, carrying caches."""
    source = iter(observations(session_id, start, seconds, definitions, seed))
    next_observation = next(source, None)
    hz = max(signal.hz for signal in definitions)
    cache = {}
    for sample_id in range(seconds * hz):
        ts = start + timedelta(microseconds=sample_id * 1_000_000 // hz)
        while next_observation is not None and next_observation.ts <= ts:
            cache[next_observation.signal_id] = next_observation.value
            next_observation = next(source, None)
        yield (sample_id + 1, int(ts.timestamp() * 1000), cache[1], cache[2], cache[3])


def batches(items, size):
    if size <= 0:
        raise ValueError("Batch size must be positive")
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
