# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Compare captures taken in different conditions (indoor vs outdoor, shade vs sun).

Per capture: throughput, share of shots with no return, the sensor's own noise flags, isolated
points (alone in a 0.5 m voxel over 1 s, the usual signature of sunlight noise), floor flatness
at 5 to 9 m, and how far the sensor sees. Noise is also broken out by 30 deg azimuth sector,
because sunlight noise concentrates toward the sun.

  uv run envcompare.py indoor.npz outdoor.npz [--height 0.58]
"""
import argparse, math

import numpy as np

from cone import level_rotation


def stats(path, height):
    d = np.load(path)
    xyz, t, tag, refl = d["xyz"].astype(np.float64), d["t"], d["tag"], d["refl"]
    dur = float(t.max() - t.min())
    n_all = len(xyz)
    nrm = np.linalg.norm(xyz, axis=1)
    ret = nrm > 0
    # Livox tag: bits 0-1 confidence from spatial position, bits 2-3 from intensity (0 = normal)
    sp, it = tag & 0x03, (tag >> 2) & 0x03
    ok = nrm > 0.3
    up = d["imu"][:, 3:].mean(0)
    up /= np.linalg.norm(up)
    p = xyz @ level_rotation(up).T
    h = p[:, 2] + height
    r = np.hypot(p[:, 0], p[:, 1])
    az = np.degrees(np.arctan2(p[:, 1], p[:, 0]))

    one = ok & (t - t.min() < 1.0)
    vox = np.floor(p[one] / 0.5).astype(np.int64)
    _, inv, cnt = np.unique(vox, axis=0, return_inverse=True, return_counts=True)
    isolated = np.zeros(len(xyz), bool)
    isolated[np.flatnonzero(one)[cnt[inv.ravel()] == 1]] = True

    floor = ok & (r > 5) & (r < 9) & (np.abs(h) < 0.08)
    if floor.sum() > 200:
        A = np.c_[p[floor, 0], p[floor, 1], np.ones(floor.sum())]
        coef, *_ = np.linalg.lstsq(A, p[floor, 2], rcond=None)
        floor_rms = float(np.std(p[floor, 2] - A @ coef)) * 100
    else:
        floor_rms = float("nan")

    out = dict(
        file=path, duration_s=dur, pts_per_s=n_all / dur, no_return_pct=100 * (1 - ret.mean()),
        flag_spatial_pct=100 * (sp[ret] > 0).mean(), flag_intensity_pct=100 * (it[ret] > 0).mean(),
        isolated_per_s=int(isolated.sum()), floor_pts=int(floor.sum()), floor_rms_cm=floor_rms,
        range_p50=float(np.percentile(r[ok], 50)), range_p99=float(np.percentile(r[ok], 99)),
        beyond_20m_pct=100 * (r[ok] > 20).mean(), refl_floor_med=float(np.median(refl[floor])) if floor.any() else float("nan"),
        gyro_dps=np.degrees(d["imu"][:, :3].mean(0)),
    )
    sectors = []
    for lo in range(-180, 180, 30):
        m = ret & (az >= lo) & (az < lo + 30)
        sectors.append((lo, int(m.sum()), 100 * (sp[m] > 0).mean() if m.any() else 0, 100 * (it[m] > 0).mean() if m.any() else 0,
                        int((isolated & (az >= lo) & (az < lo + 30)).sum())))
    out["sectors"] = sectors
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("npz", nargs="+")
    ap.add_argument("--height", type=float, default=0.58)
    a = ap.parse_args()
    S = [stats(f, a.height) for f in a.npz]
    rows = [("points/s", "pts_per_s", "{:,.0f}"), ("shots with no return %", "no_return_pct", "{:.1f}"),
            ("flagged noise, spatial %", "flag_spatial_pct", "{:.2f}"), ("flagged noise, intensity %", "flag_intensity_pct", "{:.2f}"),
            ("isolated points in 1 s", "isolated_per_s", "{:,}"), ("floor 5-9 m: points", "floor_pts", "{:,}"),
            ("floor 5-9 m: RMS cm", "floor_rms_cm", "{:.2f}"), ("floor reflectivity (median)", "refl_floor_med", "{:.0f}"),
            ("range median m", "range_p50", "{:.1f}"), ("range 99th pct m", "range_p99", "{:.1f}"), ("returns beyond 20 m %", "beyond_20m_pct", "{:.2f}")]
    names = [f.split("/")[-1].replace(".npz", "") for f in a.npz]
    print(f"{'':30}" + "".join(f"{n[:22]:>24}" for n in names))
    for label, k, fmt in rows:
        print(f"{label:30}" + "".join(f"{fmt.format(s[k]):>24}" for s in S))
    print(f"{'gyro mean deg/s x/y/z':30}" + "".join(f"{' '.join(f'{v:+.2f}' for v in s['gyro_dps']):>24}" for s in S))
    for n, s in zip(names, S):
        print(f"\n{n}: by azimuth sector (sensor frame, 0 = +x): returns, spatial-flag %, intensity-flag %, isolated in 1 s")
        for lo, cnt, fs, fi, iso in s["sectors"]:
            print(f"  {lo:+4d}..{lo+30:+4d}: {cnt:8d}  {fs:5.2f}  {fi:5.2f}  {iso:6d}")
