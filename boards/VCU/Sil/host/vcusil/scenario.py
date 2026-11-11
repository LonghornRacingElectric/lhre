"""Scripted drives: what the driver and the rest of the car do over time.

Times are integer milliseconds. A drive is a Timeline, compiled from a
scenarios/<name>.toml file by scenario_file.py; the format and its exact
semantics are in boards/VCU/Sil/README.md, section Scenarios.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from .firmware import HVC_NOT_ENERGIZED


@dataclass(frozen=True)
class Driver:
    accelerator: float = 0.0  # 0..1 of APPS travel
    brake_psi: float = 0.0  # line pressure, front and rear alike
    steering_deg: float = 0.0  # steering wheel
    rtd_pressed: bool = False


@dataclass(frozen=True)
class RestOfCar:
    """The nodes the car model does not include."""
    hvc_state: int = HVC_NOT_ENERGIZED
    contactors_closed: bool = False
    shutdown_loop_v: float = 24.0


DRIVER_SIGNALS = tuple(f.name for f in fields(Driver))
CAR_SIGNALS = tuple(f.name for f in fields(RestOfCar))
# What a signal no block sets holds: the dataclasses are the one source.
DEFAULTS = {**asdict(Driver()), **asdict(RestOfCar())}


@dataclass(frozen=True)
class Segment:
    """From t_ms the signal goes from start (the value it held) to end in a
    straight line over ramp_ms, or at once when ramp_ms is 0, then holds."""
    t_ms: int
    start: object
    end: object
    ramp_ms: int = 0


@dataclass(frozen=True)
class Timeline:
    """Every signal as a function of integer time: the cosim.Scenario a
    scenario file compiles to. tracks holds (signal, segments in time order)
    for the signals the file sets."""
    name: str
    duration_ms: int
    tracks: tuple[tuple[str, tuple[Segment, ...]], ...]

    def value(self, signal: str, t_ms: int) -> object:
        if t_ms < 0:
            raise ValueError(f'{self.name}: no value at {t_ms} ms; time starts at 0')
        current = None
        for name, segments in self.tracks:
            if name == signal:
                for segment in segments:
                    if segment.t_ms > t_ms:
                        break
                    current = segment
                break
        if current is None:
            return DEFAULTS[signal]
        if t_ms >= current.t_ms + current.ramp_ms:
            return current.end
        a, b = current.start, current.end
        v = a + (b - a) * ((t_ms - current.t_ms) / current.ramp_ms)
        # Rounding must not step past either end: they are the ABI's limits.
        return min(max(v, min(a, b)), max(a, b))

    def at(self, t_ms: int) -> tuple[Driver, RestOfCar]:
        return (Driver(**{s: self.value(s, t_ms) for s in DRIVER_SIGNALS}),
                RestOfCar(**{s: self.value(s, t_ms) for s in CAR_SIGNALS}))
