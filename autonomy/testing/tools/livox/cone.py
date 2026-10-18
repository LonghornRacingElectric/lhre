# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Find one cone in a Mid-360 capture and measure how well the sensor sees it.

Levels the cloud with IMU gravity, puts the ground at the tape-measured sensor height, keeps
points 3 to 80 cm above ground, clusters them on a 10 cm grid, and takes the most-hit compact
cluster within 1.5 m of the tape distance (optionally within +-25 deg of a bearing). The test
plan's pass line (>= 50 points in 5 s at 10 m) is written against `pts_first5s`.

  uv run cone.py capture.npz --dist 10 --height 0.58 [--bearing 180] [--figs out/prefix]
"""
import argparse
import math

import numpy as np

FOV_LOW_DEG = -7.0  # Mid-360 lower field-of-view edge, sensor frame
CONE_MAX_WIDTH_M = 0.35  # widest cluster that can still be one cone (large FSAE cone base: 285 mm)
CONE_TOP_M = (0.35, 0.65)  # cone top height window, default for the ~52 cm cone; session.json can override


def level_rotation(up):
    """Rotation taking the measured gravity-up vector to +z."""
    v = np.cross(up, [0, 0, 1.0])
    s, c = np.linalg.norm(v), up @ [0, 0, 1.0]
    if s < 1e-9:
        return np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s**2)


def analyze(npz, dist, height, bearing=None, top_m=None, max_width_m=None):
    d = np.load(npz)
    xyz, t, imu = d["xyz"].astype(np.float64), d["t"], d["imu"]
    nrm = np.linalg.norm(xyz, axis=1)
    blocked = int(((nrm > 0) & (nrm < 0.3) & (np.abs(np.degrees(np.arctan2(xyz[:, 1], xyz[:, 0]))) < 60)).sum())
    ok = nrm > 0.3
    xyz, t = xyz[ok], t[ok]
    up = imu[:, 3:].mean(0)
    up /= np.linalg.norm(up)
    p = xyz @ level_rotation(up).T
    tilt = math.degrees(math.acos(float(np.clip(up[2], -1, 1))))
    h = p[:, 2] + height
    r_xy = np.hypot(p[:, 0], p[:, 1])

    band = (h > 0.03) & (h < 0.80) & (r_xy > 1.0) & (r_xy < dist + 6)
    if bearing is not None:
        brg = np.degrees(np.arctan2(p[:, 1], p[:, 0]))
        band &= np.abs((brg - bearing + 180) % 360 - 180) < 25
    idx = np.flatnonzero(band)
    keys = {}
    for i, k in zip(idx, map(tuple, np.floor(p[idx, :2] / 0.10).astype(np.int64))):
        keys.setdefault(k, []).append(i)
    seen, clusters = set(), []
    for k in keys:
        if k in seen:
            continue
        stack, members = [k], []
        seen.add(k)
        while stack:
            cx, cy = stack.pop()
            members += keys[(cx, cy)]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (cx + dx, cy + dy)
                    if nb in keys and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        clusters.append(np.array(members))

    # The cone is the cone-shaped cluster (narrow, topping out at cone height) closest to the tape
    # distance. Point count alone is not enough: bags and feet near the cone can out-score it.
    top_m, max_width_m = top_m or CONE_TOP_M, max_width_m or CONE_MAX_WIDTH_M
    best, best_err, cands = None, 1e9, []
    for m in clusters:
        q = p[m]
        ext = np.ptp(q[:, :2], axis=0).max()
        rng = float(np.hypot(*q[:, :2].mean(0)))
        top = float(h[m].max())
        if ext > 0.6 or len(m) < 3:
            continue
        cands.append((abs(rng - dist), rng, len(m), ext, top))
        shaped = ext < max_width_m and top_m[0] < top < top_m[1]
        if shaped and len(m) >= 20 and abs(rng - dist) < min(1.5, best_err):
            best, best_err = m, abs(rng - dist)

    res = dict(npz=str(npz), tape_m=dist, sensor_height_m=height, imu_tilt_deg=round(tilt, 2), blocked_returns=blocked,
               p=p, h=h, t=t, up=up, cone_idx=best, candidates=sorted(cands)[:5], found=best is not None)
    if best is None:
        return res
    q, tq = p[best], t[best]
    cen = q[:, :2].mean(0)
    brg_c = math.degrees(math.atan2(cen[1], cen[0]))
    t0, dur = t.min(), t.max() - t.min()
    nfr = int(np.floor(dur / 0.1))
    per = np.bincount(np.floor((tq - t0) / 0.1).astype(int), minlength=nfr)[:nfr]
    # where the lower field-of-view edge crosses the cone, in the levelled frame
    u = np.array([math.cos(math.radians(FOV_LOW_DEG)) * math.cos(math.radians(brg_c)),
                  math.cos(math.radians(FOV_LOW_DEG)) * math.sin(math.radians(brg_c)), math.sin(math.radians(FOV_LOW_DEG))])
    elev_low = math.asin(float(np.clip(u @ up, -1, 1)))
    rng_c = float(np.hypot(*cen))
    res.update(range_center_m=round(rng_c, 3), near_face_m=round(float(np.hypot(q[:, 0], q[:, 1]).min()), 3),
               bearing_deg=round(brg_c, 1), width_cm=round(float(np.ptp(q[:, :2], axis=0).max()) * 100, 1),
               visible_low_cm=round(float(h[best].min()) * 100, 1), visible_high_cm=round(float(h[best].max()) * 100, 1),
               fov_floor_at_cone_cm=round((height + rng_c * math.tan(elev_low)) * 100, 1),
               pts_total=int(len(best)), duration_s=round(float(dur), 2), pts_per_frame=round(float(per.mean()), 2),
               median_per_frame=float(np.median(per)), frames_hit_pct=round(float((per > 0).mean()) * 100, 1),
               pts_first5s=int((tq - t0 < 5.0).sum()), range_bias_cm=round((rng_c - dist) * 100, 1), per_frame=per)
    return res


def figures(res, prefix, label, cone_dims=None):
    """Two presentation figures: top-down overview and the cone silhouette as the sensor sees it."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 14, "axes.titlesize": 15, "figure.dpi": 150})
    p, h = res["p"], res["h"]
    b = math.radians(res["bearing_deg"])
    cx, cy = math.cos(b) * res["range_center_m"], math.sin(b) * res["range_center_m"]

    lo = np.minimum([0, 0], [cx, cy]) - 3.0
    hi = np.maximum([0, 0], [cx, cy]) + 3.0
    span, mid = max(hi - lo), (lo + hi) / 2
    lo, hi = mid - span / 2, mid + span / 2
    m = (h > 0.03) & (h < 0.8) & (p[:, 0] > lo[0]) & (p[:, 0] < hi[0]) & (p[:, 1] > lo[1]) & (p[:, 1] < hi[1])
    fig, ax = plt.subplots(figsize=(9, 8))
    sc = ax.scatter(p[m, 0], p[m, 1], s=0.6, c=h[m], cmap="viridis", vmin=0, vmax=0.8, rasterized=True)
    ax.plot(0, 0, "r^", ms=14)
    ax.annotate("sensor", (0, 0), (10, -20), textcoords="offset points", color="r")
    ax.add_patch(plt.Circle((cx, cy), 0.45, fill=False, color="r", lw=2.5))
    ax.annotate(f"cone, {res['range_center_m']:.2f} m", (cx, cy), (0, 28), textcoords="offset points", color="r", ha="center")
    ax.add_patch(plt.Circle((0, 0), res["tape_m"], fill=False, color="0.5", ls="--", lw=1))
    ax.set(xlim=(lo[0], hi[0]), ylim=(lo[1], hi[1]), aspect="equal", xlabel="x (m)", ylabel="y (m)",
           title=f"{label}: top down, {res['duration_s']:.0f} s\npoints 0 to 0.8 m above ground, dashed = tape distance")
    fig.colorbar(sc, ax=ax, shrink=0.8, label="height above ground (m)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(f"{prefix}_overview.png")
    plt.close(fig)

    q, hq = p[res["cone_idx"]], h[res["cone_idx"]]
    right = (q[:, 0] - cx) * math.sin(b) - (q[:, 1] - cy) * math.cos(b)  # + = right as seen from the sensor
    fig, ax = plt.subplots(figsize=(7, 8))
    ax.scatter(right * 100, hq * 100, s=14, c="tab:orange", edgecolors="none")
    if cone_dims:
        w, ht = cone_dims
        ax.plot([-w * 50, 0, w * 50], [0, ht * 100, 0], "k--", lw=1, label=f"nominal cone, {w*1000:.0f} x {ht*1000:.0f} mm")
    ax.axhline(res["fov_floor_at_cone_cm"], color="tab:blue", ls=":", lw=1.5, label="nominal field-of-view floor (-7 deg)")
    ax.axhline(0, color="k", lw=1.2)
    ax.set(xlim=(-40, 40), ylim=(-5, 75), aspect="equal", xlabel="left / right as seen from the sensor (cm)",
           ylabel="height above ground (cm)",
           title=f"{label}\n{res['pts_total']} points in {res['duration_s']:.0f} s, {res['pts_per_frame']:.1f} per 0.1 s frame")
    ax.legend(loc="upper right", fontsize=11)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(f"{prefix}_silhouette.png")
    plt.close(fig)


def report(res):
    print(f"{res['npz']}: tape {res['tape_m']:.2f} m, sensor {res['sensor_height_m']:.2f} m, IMU tilt {res['imu_tilt_deg']:.1f} deg")
    if res["blocked_returns"] > 1000:
        print(f"WARNING: {res['blocked_returns']} returns within 0.3 m of the sensor on its +x side: something is next to the window")
    if not res["found"]:
        print("no cone-shaped cluster within 1.5 m of the tape distance; nearest candidates (err, range, points, width, top):")
        for c in res["candidates"]:
            print("  ", tuple(round(float(v), 2) for v in c))
        return
    print(f"cone at {res['range_center_m']:.2f} m (near face {res['near_face_m']:.2f} m), bearing {res['bearing_deg']:.0f} deg, "
          f"{res['width_cm']:.0f} cm wide, visible {res['visible_low_cm']:.0f} to {res['visible_high_cm']:.0f} cm above ground "
          f"(field of view floor {res['fov_floor_at_cone_cm']:.0f} cm)")
    print(f"points: {res['pts_total']} in {res['duration_s']:.1f} s = {res['pts_per_frame']:.2f} per frame "
          f"(median {res['median_per_frame']:.0f}, {res['frames_hit_pct']:.0f}% of frames hit), first 5 s: {res['pts_first5s']}")
    print(f"range bias (cone center - tape): {res['range_bias_cm']:+.1f} cm")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("npz")
    ap.add_argument("--dist", type=float, required=True, help="tape distance to the cone center, m")
    ap.add_argument("--height", type=float, required=True, help="tape-measured sensor height above ground, m")
    ap.add_argument("--bearing", type=float, default=None, help="cone bearing in the sensor frame, deg (0 = +x)")
    ap.add_argument("--figs", default=None, help="write <prefix>_overview.png and <prefix>_silhouette.png")
    ap.add_argument("--label", default="cone")
    a = ap.parse_args()
    r = analyze(a.npz, a.dist, a.height, a.bearing)
    report(r)
    if r["found"] and a.figs:
        figures(r, a.figs, a.label)
