#!/usr/bin/env python3
"""
Livox Mid-360 beam pattern and range model.

No ROS imports on purpose: this runs and is tested anywhere numpy does,
including a macOS laptop with no ROS install, which is where the sensor
work actually happens.

Held exactly, from the datasheet:

- 360 degree horizontal field of view.
- The asymmetric vertical field of view, -7 to +52 degrees. The sensor
  looks mostly *up*, which is why mount pitch decides whether it sees
  cones at all, and why the mount study exists.
- The point rate, 200 kHz, and so the points per frame.
- Non-repetition: the pattern never closes on itself, so successive
  frames sample different directions and dwelling longer keeps adding
  coverage.

A model, and not the real device:

- The beam's path *inside* that field of view. Livox does not publish
  the Risley-prism geometry, so two incommensurate sweeps stand in for
  it. That reproduces the non-repetition, the scan-line structure a
  clustering algorithm sees, and a plausible angular density, but it is
  not their curve.
- Because the density is a guess, any conclusion drawn from this model
  should be re-run with ``elevation_profile='uniform'``. If it survives
  both, it does not rest on the guess. See the README.
"""

from dataclasses import dataclass
import math

import numpy as np

# Ratio between the two sweeps. Irrational, so the pattern never closes
# on itself: that non-repetition is the Mid-360's defining property and
# the reason a uniform-grid sensor model cannot stand in for it.
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
    # 'rosette' bunches points toward the field of view edges, as an
    # oscillating scanner does. 'uniform' spreads them evenly in
    # elevation and exists to test whether a result depends on this.
    elevation_profile: str = 'rosette'

    def points_per_frame(self) -> int:
        """Return how many beams one frame contains."""
        return int(round(self.point_rate_hz / self.frame_rate_hz))


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
        f"elevation_profile must be 'rosette' or 'uniform', got {profile!r}")


def beam_angles(cfg: Mid360Config, t_start: float,
                count: int = 0) -> tuple:
    """
    Return the azimuth and elevation of each beam, in radians.

    Angles come from absolute time, not from a per-frame counter, so
    non-repetition across frames falls out of the incommensurate sweeps
    rather than being bolted on. Two frames an exact second apart are
    still different, which is the property that matters.
    """
    if count <= 0:
        count = cfg.points_per_frame()

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
