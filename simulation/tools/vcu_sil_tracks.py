import csv
import math
from dataclasses import dataclass
from pathlib import Path

STRAIGHT = {"vcu.useMode": 1, "vcu.steerAmp": 0}


@dataclass(frozen=True)
class Track:
    description: str
    initial_speed_m_s: float
    stop_time_s: float
    steer: dict[str, float]
    pedals: tuple[tuple[float, float, float], ...]


TRACKS = {
    "coast": Track(
        "Straight from 15 m/s with no pedals.",
        initial_speed_m_s=15.0,
        stop_time_s=5.0,
        steer=STRAIGHT,
        pedals=((0.0, 0.0, 0.0),),
    ),
    "launch": Track(
        "Straight from rest. The accelerator goes from 0 to 100 % between 0.5 and 0.6 s.",
        initial_speed_m_s=0.0,
        stop_time_s=3.0,
        steer=STRAIGHT,
        pedals=((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (0.6, 1.0, 0.0)),
    ),
    "corner-exit": Track(
        "Sine steer from 10 m/s, 20 deg handwheel at 0.1 Hz from 0.5 s. "
        "The accelerator goes to 100 % at the steer peak (3 s) as the steer unwinds.",
        initial_speed_m_s=10.0,
        stop_time_s=5.0,
        steer={"vcu.useMode": 1, "vcu.steerStart": 0.5, "vcu.steerFreq": 0.1, "vcu.steerAmp": math.radians(20)},
        pedals=((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (3.1, 1.0, 0.0)),
    ),
}


def track_errors(track: Track) -> list[str]:
    if not track.pedals or track.pedals[0][0] != 0:
        return ["The trace must start at 0 s."]
    errors = [
        f"The trace times must increase. {time:g} s follows {previous[0]:g} s."
        for previous, (time, _, _) in zip(track.pedals, track.pedals[1:])
        if time <= previous[0]
    ]
    for time, accelerator, brake in track.pedals:
        errors += [
            f"The {pedal} at {time:g} s is {value:g}, not in [0, 1]."
            for pedal, value in (("accelerator", accelerator), ("brake", brake))
            if not 0 <= value <= 1
        ]
    return errors


def write_driver_trace(path: Path, track: Track) -> None:
    with path.open("w", newline="") as trace_file:
        writer = csv.writer(trace_file, lineterminator="\n")
        writer.writerow(["time_s", "accelerator", "brake"])
        writer.writerows(track.pedals)
