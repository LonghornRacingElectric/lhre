# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Split cone hits into body (above 8 cm) and base, with reflectivity, to compare runs taken in different
conditions. The base hit count depends on how the cone is turned and on the floor, the body's on the sensor.

  uv run bodybase.py small_5p08m_level small_out_5p08m_level [--top 0.18 0.45 --width 0.30]
"""
import argparse, pathlib, re

import numpy as np

import cone

D = pathlib.Path.home() / "lhr-test-data" / "current" / "raw"

ap = argparse.ArgumentParser()
ap.add_argument("runs", nargs="+", help="run names in the current campaign's raw/ folder")
ap.add_argument("--height", type=float, default=0.58, help="sensor height above ground, m")
ap.add_argument("--top", type=float, nargs=2, default=(0.18, 0.45), help="cone top height window, m (default: small cone)")
ap.add_argument("--width", type=float, default=0.30, help="widest cluster that is still one cone, m")
a = ap.parse_args()

print(f"{'run':30} {'all/frame':>9} {'body/frame':>10} {'base/frame':>10} {'body width cm':>13} {'refl body med':>13}")
for n in a.runs:
    dist = float(re.search(r"_(\d+p?\d*)m_", n).group(1).replace("p", "."))  # name carries the tape distance
    r = cone.analyze(D / f"{n}.npz", dist, a.height, 180, tuple(a.top), a.width)
    if not r["found"]:
        print(f"{n:30} no cone found")
        continue
    d = np.load(D / f"{n}.npz")
    refl = d["refl"][np.linalg.norm(d["xyz"], axis=1) > 0.3][r["cone_idx"]]
    hh, q = r["h"][r["cone_idx"]], r["p"][r["cone_idx"]]
    body, frames = hh > 0.08, r["duration_s"] / 0.1
    w = np.ptp(q[body, :2], axis=0).max() * 100
    print(f"{n:30} {len(hh)/frames:9.2f} {body.sum()/frames:10.2f} {(~body).sum()/frames:10.2f} {w:13.1f} {np.median(refl[body]):13.0f}")
