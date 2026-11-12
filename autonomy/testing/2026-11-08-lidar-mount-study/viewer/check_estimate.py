"""
Check the viewer's points-on-cone estimate against lhr_lidar_sim's full model.

    python3 check_estimate.py <autonomy>/ros2/src

The viewer multiplies beam density by a cone's size as seen from the sensor.
The sim casts every beam. Neither has the car in the way here. The viewer's
notes quote the result: within about 20%, up to about 40% low where the
densest band lands.
"""
import gzip
import math
import sys

import numpy as np

SRC = sys.argv[1]
sys.path[:0] = [f'{SRC}/lhr_lidar_sim', f'{SRC}/lhr_vehicle', f'{SRC}/lhr_trackgen']
from lhr_lidar_sim.mid360 import Mid360Config  # noqa: E402
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose, points_on_cone  # noqa: E402

rows = np.loadtxt(gzip.open(f'{SRC}/lhr_lidar_sim/lhr_lidar_sim/patterns/mid360.csv.gz', 'rt'), delimiter=',', skiprows=1)
el = 90.0 - rows[:, 2]
BIN = 0.2
edges = np.arange(-7.5, 52.5 + BIN, BIN)
counts, _ = np.histogram(el, edges)
dens = counts / 40.0 / (360.0 * BIN)        # beams per square degree per frame


def density(el_deg):
    i = int((el_deg - edges[0]) // BIN)
    return dens[i] if 0 <= i < len(dens) else 0.0


H, X = 1.06, -0.94 + 1.5494
NOSE = (0.955 + 1.5494) - X
CONE_H, BASE_W, TOP_W, SLICES = 0.325, 0.228, 0.04, 16


def analytic(roll, pitch, d_nose):
    up = np.array([0.0, 0.0, -1.0 if roll else 1.0])     # sensor up in a pitch-free frame
    p = math.radians(pitch)
    # sensor up after nose-down pitch: rotate about lateral axis
    up = np.array([up[2] * math.sin(p), 0.0, up[2] * math.cos(p)])
    d = d_nose + NOSE
    total = 0.0
    for k in range(SLICES):
        z = (k + 0.5) * CONE_H / SLICES
        v = np.array([d, 0.0, z - H]); r = np.linalg.norm(v); v /= r
        el_s = math.degrees(math.asin(float(v @ up)))
        w = BASE_W - (BASE_W - TOP_W) * z / CONE_H
        area_deg2 = w * (CONE_H / SLICES) / r ** 2 * (180 / math.pi) ** 2
        total += density(el_s) * area_deg2
    return total


opts = [('upright level', 0, 0), ('upright 6 down', 0, 6), ('upright 8 down', 0, 8),
        ('upright 20 down', 0, 20), ('upside down', 180, 0)]
ds = [3, 4, 5, 6, 8, 10, 12, 15]
print('mount'.ljust(17) + ''.join(f'{d:>11} m' for d in ds))
ratios = []
for name, roll, pitch in opts:
    s = Mid360Sensor(config=Mid360Config(), mount=MountPose(x_m=X, y_m=0, z_m=H, roll_rad=math.radians(roll), pitch_rad=math.radians(pitch)))
    cells = []
    for d in ds:
        sim = points_on_cone(s, 0.0, d + NOSE, frames=40) / 40
        an = analytic(roll, pitch, d)
        if sim > 0.3 and an > 0.3:
            ratios.append(sim / an)
        cells.append(f'{sim:5.1f}/{an:5.1f}')
    print(name.ljust(17) + ''.join(f'{c:>13}' for c in cells))
print(f'sim/analytic ratio: median {np.median(ratios):.2f}, range {min(ratios):.2f}..{max(ratios):.2f} (n={len(ratios)})')
