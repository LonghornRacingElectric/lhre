# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Plane checks from a capture: flatness, room-corner dihedral angles, and IMU gravity vs walls/ceiling.

Fits the largest planes with RANSAC over --window seconds of points, classifies each as
horizontal or vertical against gravity from the IMU, and reports the angles the test plan asks for.
"""
import argparse
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("npz")
ap.add_argument("--window", type=float, default=1.0)
ap.add_argument("--planes", type=int, default=6)
ap.add_argument("--thresh", type=float, default=0.02)
ap.add_argument("--min-inliers", type=int, default=3000)
a = ap.parse_args()

d = np.load(a.npz)
xyz, t, acc = d["xyz"].astype(np.float64), d["t"], d["imu"][:, 3:]
ok = (np.linalg.norm(xyz, axis=1) > 0.3) & (t - t.min() < a.window)
pts = xyz[ok]
up = acc.mean(0) / np.linalg.norm(acc.mean(0))  # accelerometer at rest reads +1 g along "up"
print(f"{len(pts):,} points in {a.window:.1f} s; gravity-up in sensor frame {np.round(up, 4)}  "
      f"(tilt from sensor z {np.degrees(np.arccos(up[2])):.2f} deg)")

rng = np.random.default_rng(0)


def fit(p):
    c = p.mean(0)
    _, _, vt = np.linalg.svd(p - c, full_matrices=False)
    n = vt[2]
    return n, -n @ c


planes, rest = [], pts.copy()
for _ in range(a.planes):
    if len(rest) < a.min_inliers:
        break
    best, best_n = None, 0
    for _ in range(400):
        s = rest[rng.choice(len(rest), 3, replace=False)]
        n = np.cross(s[1] - s[0], s[2] - s[0])
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        inl = np.abs(rest @ n - n @ s[0]) < a.thresh
        k = int(inl.sum())
        if k > best_n:
            best, best_n = inl, k
    if best_n < a.min_inliers:
        break
    n, off = fit(rest[best])
    inl = np.abs(rest @ n + off) < a.thresh
    n, off = fit(rest[inl])
    res = rest[inl] @ n + off
    p = rest[inl]
    tilt = np.degrees(np.arccos(min(1.0, abs(n @ up))))  # 0 = horizontal plane, 90 = vertical
    kind = "horizontal" if tilt < 20 else "vertical" if tilt > 70 else "slanted"
    # extent along the plane's two in-plane axes
    _, _, vt = np.linalg.svd(p - p.mean(0), full_matrices=False)
    span = np.ptp(p @ vt[0]), np.ptp(p @ vt[1])
    planes.append(dict(n=n, off=off, k=int(inl.sum()), rms=res.std(), kind=kind, tilt=tilt, c=p.mean(0), span=span,
                       r=np.linalg.norm(p, axis=1).mean()))
    rest = rest[~inl]

print(f"\n{'#':>2} {'kind':10} {'points':>7} {'RMS cm':>6} {'range m':>7} {'span m':>11} {'angle to gravity':>17}  center (m)")
for i, p in enumerate(planes):
    dev = p["tilt"] if p["kind"] == "horizontal" else 90 - p["tilt"]
    print(f"{i:>2} {p['kind']:10} {p['k']:>7} {p['rms']*100:6.2f} {p['r']:7.2f} {p['span'][0]:5.2f}x{p['span'][1]:<5.2f} "
          f"{'off-level' if p['kind']=='horizontal' else 'off-plumb'} {dev:5.2f} deg  {np.round(p['c'], 2)}")

walls = [p for p in planes if p["kind"] == "vertical"]
print("\nwall-to-wall dihedral angles (90 = square corner):")
for i in range(len(walls)):
    for j in range(i + 1, len(walls)):
        ang = np.degrees(np.arccos(min(1.0, abs(walls[i]["n"] @ walls[j]["n"]))))
        label = "parallel pair" if ang < 20 else "corner"
        print(f"  wall @{np.round(walls[i]['c'][:2], 1)} vs wall @{np.round(walls[j]['c'][:2], 1)}: {ang:6.2f} deg  ({label})")
hz = [p for p in planes if p["kind"] == "horizontal"]
for h in hz:
    for w in walls:
        ang = np.degrees(np.arccos(min(1.0, abs(h["n"] @ w["n"]))))
        print(f"  ceiling/floor @z={h['c'][2]:.2f} vs wall @{np.round(w['c'][:2], 1)}: {ang:6.2f} deg")
