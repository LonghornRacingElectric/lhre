# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Long-range check: list small objects along the cone line in a run and in a background run without
the cone there, so a far cone is told apart from bushes and curbs by being new.

  uv run farcheck.py <run> <background_run> --near 12 --far 25
"""
import argparse, pathlib

import numpy as np

from cone import level_rotation

D = pathlib.Path.home() / "lhr-test-data" / "current" / "raw"


def objects(name, near, far, height=0.58, bearing=180):
    d = np.load(D / f"{name}.npz")
    xyz, t = d["xyz"].astype(float), d["t"]
    ok = np.linalg.norm(xyz, axis=1) > 0.3
    xyz, t = xyz[ok], t[ok]
    up = d["imu"][:, 3:].mean(0)
    up /= np.linalg.norm(up)
    p = xyz @ level_rotation(up).T
    h = p[:, 2] + height
    r = np.hypot(p[:, 0], p[:, 1])
    b = np.degrees(np.arctan2(p[:, 1], p[:, 0]))
    m = (h > 0.03) & (h < 0.9) & (np.abs((b - bearing + 180) % 360 - 180) < 20) & (r > near) & (r < far)
    q, hq, tq = p[m], h[m], t[m]
    keys = {}
    for i, k in enumerate(map(tuple, np.floor(q[:, :2] / 0.15).astype(int))):
        keys.setdefault(k, []).append(i)
    seen, out = set(), []
    for k in keys:
        if k in seen:
            continue
        st, mem = [k], []
        seen.add(k)
        while st:
            c = st.pop()
            mem += keys[c]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (c[0] + dx, c[1] + dy)
                    if nb in keys and nb not in seen:
                        seen.add(nb)
                        st.append(nb)
        mem = np.array(mem)
        cen = q[mem, :2].mean(0)
        dur = t.max() - t.min()
        out.append(dict(range=float(np.hypot(*cen)), bearing=float(np.degrees(np.arctan2(cen[1], cen[0]))), n=len(mem),
                        per_s=len(mem) / dur, width=float(np.ptp(q[mem, :2], axis=0).max()), lo=float(hq[mem].min()),
                        hi=float(hq[mem].max()), first5=int((tq[mem] - t.min() < 5).sum()), xy=cen))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("background")
    ap.add_argument("--near", type=float, default=12)
    ap.add_argument("--far", type=float, default=25)
    a = ap.parse_args()
    run, bg = objects(a.run, a.near, a.far), objects(a.background, a.near, a.far)
    print(f"objects within 20 deg of -x, {a.near:g} to {a.far:g} m, 3 to 90 cm tall ({a.run} vs background {a.background}):")
    for o in sorted(run, key=lambda o: o["range"]):
        new = all(np.hypot(*(o["xy"] - g["xy"])) > 0.4 for g in bg)
        if o["n"] >= 3:
            print(f"  {'NEW ' if new else '    '}range {o['range']:5.2f} m  bearing {o['bearing']:+5.0f}  {o['n']:4d} pts ({o['per_s']:.1f}/s, "
                  f"first 5 s {o['first5']:3d})  {o['width']*100:3.0f} cm wide  {o['lo']*100:3.0f}..{o['hi']*100:3.0f} cm")
