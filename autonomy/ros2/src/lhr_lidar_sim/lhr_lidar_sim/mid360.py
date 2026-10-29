#!/usr/bin/env python3
"""
Replay Livox's Mid-360 beam table and apply a simplified range model.

The default angles come from the MIT-licensed official Livox simulator.
Legacy synthetic sweeps remain available for sensitivity studies. No ROS
imports are needed to test the geometry or replay the bundled table.
"""

from dataclasses import dataclass
from functools import lru_cache
import gzip
import math
from pathlib import Path

import numpy as np

# Legacy synthetic sweeps use incommensurate frequencies.
GOLDEN_RATIO = (1.0 + math.sqrt(5.0)) / 2.0


@dataclass(frozen=True)
class Mid360Config:
    """Mid-360 parameters. Datasheet values are the defaults."""

    point_rate_hz: float = 200_000.0
    frame_rate_hz: float = 10.0
    el_min_deg: float = -7.0
    el_max_deg: float = 52.0
    min_range_m: float = 0.1
    max_range_m: float = 40.0
    # One standard deviation of range error. The datasheet quotes 2 cm
    # at 10 m; it is applied along the beam, not to the xyz components.
    range_noise_std_m: float = 0.02
    # Fraction of returns lost outright. A dark cone at range reflects
    # too little to trigger a return at all, which matters more to a
    # detector than range error does.
    dropout_rate: float = 0.0
    # Revolutions per second of the scanning assembly. A free parameter
    # of the model, not a datasheet number: it sets how many azimuth
    # sweeps land in one frame and therefore the scan-line spacing.
    az_sweep_hz: float = 400.0
    # 'livox' replays the vendor table; the others are synthetic studies.
    elevation_profile: str = 'livox'
    return_profile: str = 'baseline'

    def points_per_frame(self) -> int:
        """Return how many beams one frame contains."""
        return int(round(self.point_rate_hz / self.frame_rate_hz))


@lru_cache(maxsize=1)
def _livox_angles() -> np.ndarray:
    """Load the bundled vendor table once, in sensor-frame radians."""
    path = Path(__file__).parent / 'patterns' / 'mid360.csv.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        table = np.loadtxt(stream, delimiter=',', skiprows=1, usecols=(1, 2))
    # Livox uses pitch = zenith - 90 degrees to rotate its forward ray.
    # Positive pitch points down, whereas our elevation is positive up.
    table[:, 1] = 90.0 - table[:, 1]
    angles = np.deg2rad(table)
    angles[:, 0] %= 2.0 * np.pi
    angles.setflags(write=False)
    return angles


def _elevation_shape(phase: np.ndarray, profile: str) -> np.ndarray:
    """
    Map sweep phase to a shape in [-1, 1].

    A sine spends more time near its turning points, so beams bunch
    toward the edges of the vertical field of view. A triangle wave has
    constant slope and so samples elevation evenly. Both are continuous
    sweeps and both give scan lines; they differ only in where the
    density goes, which is exactly the thing worth testing against.
    """
    if profile == 'rosette':
        return np.sin(phase)
    if profile == 'uniform':
        return (2.0 / np.pi) * np.arcsin(np.sin(phase))
    raise ValueError(
        f"elevation_profile must be 'livox', 'rosette' or 'uniform', got {profile!r}")


def beam_angles(cfg: Mid360Config, t_start: float,
                count: int = 0) -> tuple:
    """
    Return the azimuth and elevation of each beam, in radians.

    Absolute time selects a continuous sequence of vendor rows, wrapping
    at the end of the four-second table at the nominal point rate. The
    CSV's first column counts samples despite its Time/s heading; timing
    follows point_rate_hz, as in the vendor plugin. Changing that rate
    changes playback speed. Synthetic profiles use the legacy sweeps.
    """
    if count <= 0:
        count = cfg.points_per_frame()

    if cfg.elevation_profile == 'livox':
        angles = _livox_angles()
        # Avoid skipping/repeating a row at floating-point frame boundaries.
        start = math.floor(t_start * cfg.point_rate_hz + 1e-6)
        indices = (start + np.arange(count, dtype=np.int64)) % len(angles)
        return angles[indices, 0], angles[indices, 1]

    t = t_start + np.arange(count, dtype=np.float64) / cfg.point_rate_hz

    az = (2.0 * np.pi * cfg.az_sweep_hz * t) % (2.0 * np.pi)

    el_mid = math.radians(cfg.el_max_deg + cfg.el_min_deg) / 2.0
    el_half = math.radians(cfg.el_max_deg - cfg.el_min_deg) / 2.0
    el_phase = 2.0 * np.pi * (cfg.az_sweep_hz / GOLDEN_RATIO) * t
    el = el_mid + el_half * _elevation_shape(el_phase,
                                             cfg.elevation_profile)
    return az, el


def unit_directions(az: np.ndarray, el: np.ndarray) -> np.ndarray:
    """Return an (N, 3) array of unit beam directions in sensor frame."""
    cos_el = np.cos(el)
    return np.column_stack((
        cos_el * np.cos(az),
        cos_el * np.sin(az),
        np.sin(el),
    ))


def apply_range_model(ranges: np.ndarray, hit: np.ndarray,
                      cfg: Mid360Config,
                      rng: np.random.Generator) -> np.ndarray:
    """
    Add range noise and drop returns, in place on a copy of ``hit``.

    Returns the surviving-hit mask. Noise goes on the range along the
    beam because that is what a time-of-flight sensor gets wrong; the
    caller turns range back into xyz afterwards, so the error lands
    along the ray as it does on the real device.
    """
    hit = hit.copy()
    if cfg.range_noise_std_m > 0.0:
        noise = rng.normal(0.0, cfg.range_noise_std_m, size=ranges.shape)
        ranges[hit] += noise[hit]

    # Clip after noise: a noisy sample must still be a range the sensor
    # could have reported.
    hit &= (ranges >= cfg.min_range_m) & (ranges <= cfg.max_range_m)

    if cfg.dropout_rate > 0.0:
        kept = rng.random(ranges.shape) >= cfg.dropout_rate
        hit &= kept
    return hit
