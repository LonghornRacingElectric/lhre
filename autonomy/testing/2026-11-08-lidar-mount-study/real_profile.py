#!/usr/bin/env python3
"""
Compare the real Mid-360's returns by elevation with Livox's scan table.

    python3 real_profile.py <lhr_lidar_sim patterns>/mid360.csv.gz <10/4 raw>/cone_05m_level.npz

The garage capture loses beams in some directions (open door, sky, far
walls), so only the 10-degree azimuth sectors where at least 97% of beams
came back are compared. Those sectors are what pitch_sweep.py uses.
"""

import gzip
import sys

import numpy as np

rows = np.loadtxt(gzip.open(sys.argv[1], 'rt'), delimiter=',', skiprows=1)
tab_el = 90.0 - rows[:, 2]
d = np.load(sys.argv[2])
xyz, frames = d['xyz'].astype(np.float64), float(d['dur']) * 10.0
p = xyz[np.linalg.norm(xyz, axis=1) > 0.05]
el = np.degrees(np.arctan2(p[:, 2], np.hypot(p[:, 0], p[:, 1])))
az = np.degrees(np.arctan2(p[:, 1], p[:, 0])) % 360

SECT = 10
returned = np.histogram(az, np.arange(0, 361, SECT))[0] / frames / (20000 / 36.0)
keep = [i for i in range(36) if returned[i] >= 0.97]
print(f'sectors with >= 97% returns: {[SECT * i for i in keep]}')
mask = np.isin((az // SECT).astype(int), keep)
BIN = 1.0
edges = np.arange(-8, 54 + BIN, BIN)
real = np.histogram(el[mask], edges)[0] / frames / (SECT * len(keep) * BIN)
tab = np.histogram(tab_el, edges)[0] / (len(rows) / 20000.0) / (360 * BIN)
sel = tab > 0.05
print(f'shape correlation: {np.corrcoef(real[sel], tab[sel])[0, 1]:.3f}')
print('band (deg)     table   real   real/table   (beams per sq.deg per frame)')
for lo, hi in [(-8, -5), (-5, -1), (-1, 2), (2, 10), (10, 20), (20, 30), (30, 40),
               (40, 42), (42, 49), (49, 53)]:
    m = (edges[:-1] >= lo) & (edges[:-1] < hi)
    print(f'{lo:+3d} to {hi:+3d}      {tab[m].mean():5.2f}  {real[m].mean():5.2f}   '
          f'{real[m].mean() / tab[m].mean():5.2f}')
fine = np.arange(-8, 6, 0.2)
print(f'dense band peak: real {fine[np.argmax(np.histogram(el[mask], fine)[0])] + 0.1:+.1f}, '
      f'table {fine[np.argmax(np.histogram(tab_el, fine)[0])] + 0.1:+.1f} deg')
