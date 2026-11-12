#!/usr/bin/env python3
"""
Sweep nose-down pitch for a Mid-360 mounted upside down just inside the main hoop.

    python3 pitch_sweep.py <10/4 raw>/cone_05m_level.npz > results.csv

The density comes from a real capture, not Livox's table: the 10/4 garage
run, in the five 10-degree azimuth sectors where at least 97% of beams came
back (real_profile.py picks them). Upside down, a beam at sensor elevation e
leaves at world elevation -(e + pitch) straight ahead, so pitch moves the
dense band (peak -3.5 degrees in the sensor frame) along the ground.
Distances are metres past the nose tip. Points on a cone are density times
the cone's solid angle, before range dropout and with no car in the way.
"""

import csv
import math
import sys

import numpy as np

d = np.load(sys.argv[1])
xyz, frames = d['xyz'].astype(np.float64), float(d['dur']) * 10.0
p = xyz[np.linalg.norm(xyz, axis=1) > 0.05]
el = np.degrees(np.arctan2(p[:, 2], np.hypot(p[:, 0], p[:, 1])))
az = np.degrees(np.arctan2(p[:, 1], p[:, 0])) % 360
SECTORS = [19, 21, 22, 23, 27]          # 190, 210, 220, 230 and 270 degrees
m = np.isin((az // 10).astype(int), SECTORS)
BIN = 0.2
edges = np.arange(-8, 53 + BIN, BIN)
raw = np.histogram(el[m], edges)[0] / frames / (10 * len(SECTORS) * BIN)
dens = np.convolve(raw, np.ones(3) / 3, mode='same')   # 0.6 degree smoothing
EL_LO, EL_HI = -7.21, 52.16             # the scan table's elevation limits

H = 1.06                                # sensor centre above the ground
NOSE = (0.955 + 1.5494) - (-0.94 + 1.5494)   # nose tip ahead of the sensor
CONE_H, BASE_W, TOP_W, SLICES = 0.325, 0.228, 0.04, 16
PEAK = -3.5                             # dense band peak, real capture


def density(e):
    i = int((e - edges[0]) // BIN)
    return dens[i] if 0 <= i < len(dens) and EL_LO <= e <= EL_HI else 0.0


def points(pitch, d_nose):
    """Return beams per frame on a small cone d_nose past the nose."""
    dist = d_nose + NOSE
    total = 0.0
    for k in range(SLICES):
        z = (k + 0.5) * CONE_H / SLICES
        world_el = math.degrees(math.atan2(z - H, dist))
        width = BASE_W - (BASE_W - TOP_W) * z / CONE_H
        solid = width * (CONE_H / SLICES) / (dist ** 2 + (z - H) ** 2) * (180 / math.pi) ** 2
        total += density(-(world_el + pitch)) * solid
    return total


def lands(sensor_el, pitch):
    """Return where a beam at this sensor elevation meets the ground, past the nose."""
    w = -sensor_el - pitch
    return H / math.tan(math.radians(-w)) - NOSE if w < 0 else math.inf


def reach(pitch, per_frame):
    ok = [x for x in np.arange(2, 60, 0.25) if points(pitch, x) >= per_frame]
    return max(ok) if ok else 0.0


def main():
    out = csv.writer(sys.stdout, lineterminator='\n')
    out.writerow(['pitch_deg', 'dense_peak_lands_m', 'band_m5_m1_from_m', 'band_m5_m1_to_m',
                  'cone_top_cut_past_m', 'pts_5m', 'pts_10m', 'pts_15m', 'pts_20m',
                  'reach_1pt_m', 'reach_0p3pt_m', 'peak_at_body_pitch_m1_m', 'peak_at_body_pitch_p1_m'])
    fmt = lambda x: 'sky' if x == math.inf else f'{x:.1f}'
    for pitch in np.arange(4.0, 12.01, 0.5):
        top = -EL_LO - pitch            # the field of view's top edge, world elevation
        cut = (H - CONE_H) / math.tan(math.radians(-top)) - NOSE if top < 0 else math.inf
        out.writerow([f'{pitch:.1f}', fmt(lands(PEAK, pitch)), fmt(lands(-1.0, pitch)),
                      fmt(lands(-5.0, pitch)), fmt(cut),
                      *(f'{points(pitch, x):.2f}' for x in (5, 10, 15, 20)),
                      f'{reach(pitch, 1.0):.1f}', f'{reach(pitch, 0.3):.1f}',
                      fmt(lands(PEAK, pitch - 1)), fmt(lands(PEAK, pitch + 1))])


if __name__ == '__main__':
    main()
