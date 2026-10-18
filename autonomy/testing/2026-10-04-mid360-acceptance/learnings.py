# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Second-pass figures for the 2026-10-04 campaign: what the data says beyond points per frame.

  gyro_bias_vs_temp.png     gyro bias at rest against core temperature, every recording
  daylight_penalty.png      cone-body hits outdoors against the garage, by range
  mount_height.png          near-field blind zone against mount height and pitch, fitted to the runs
  frames_to_detect.png      how many 0.1 s frames to stack before a small cone is seen reliably
  session_timeline.png      throughput and core temperature across the session, with every outage

  uv run learnings.py [campaign_data_dir]     (default ~/lhr-test-data/2026-10-04-mid360-acceptance)
"""
import csv, datetime as dt, math, pathlib, sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "livox"))
import cone  # noqa: E402

DATA = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path.home() / "lhr-test-data" / "2026-10-04-mid360-acceptance")
RAW, FIG = DATA / "raw", DATA / "figures"
HEIGHT = 0.58
SMALL = dict(top_m=(0.18, 0.45), max_width_m=0.30)
LARGE = dict(top_m=(0.35, 0.65), max_width_m=0.35)

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#a8a7a0", "#e6e5e0"
BLUE, ORANGE, AQUA, YELLOW, CRIT = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#d03b3b"
plt.rcParams.update({"font.size": 13, "axes.titlesize": 15, "figure.dpi": 150, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
                     "axes.facecolor": SURFACE, "figure.facecolor": SURFACE})

runs = {r["run"]: r for r in csv.DictReader((DATA / "runs.csv").open())}


def finish(fig, ax_list, name, title):
    for ax in ax_list:
        ax.grid(axis="y", color=GRID, lw=1)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle(title, x=0.01, ha="left", fontsize=16)
    fig.tight_layout()
    fig.savefig(FIG / name)
    plt.close(fig)
    print("wrote", FIG / name)


def health():
    rows = []
    for f in sorted((RAW / "health").glob("endurance_*.csv")):
        for r in csv.DictReader(f.open()):
            try:
                rows.append((dt.datetime.fromisoformat(r["wall_time"]), int(r["pts_per_s"]), float(r["core_temp_c"]), int(r["lost_1s"])))
            except ValueError:
                continue
    rows.sort()
    return rows


def temp_at(hl, when):
    times = np.array([h[0].timestamp() for h in hl])
    i = int(np.argmin(np.abs(times - when.timestamp())))
    return hl[i][2] if abs(times[i] - when.timestamp()) < 60 else None


# ---------------------------------------------------------------- gyro bias against temperature
def gyro_vs_temp(hl):
    pts = []
    known = {"indoor_health_1426": (dt.datetime(2026, 10, 4, 14, 26, 10), 46.3),  # before the health log; temp from the capture
             "outdoor_baseline_1640": (dt.datetime(2026, 10, 4, 16, 40, 5), None)}
    for f in sorted(RAW.glob("*.npz")):
        name = f.stem
        d = np.load(f)
        if len(d["imu"]) < 100:
            continue
        g = np.degrees(d["imu"][:, :3].mean(0))
        if name in runs:
            end = dt.datetime.fromisoformat(runs[name]["recorded"])
            when = end - dt.timedelta(seconds=float(runs[name]["duration_s"] or 10) / 2)
            temp = temp_at(hl, when)
        else:
            when, temp = known.get(name, (None, None))
            temp = temp if temp is not None else (temp_at(hl, when) if when else None)
        if temp is not None:
            pts.append((temp, *g, name))
    T = np.array([p[0] for p in pts])
    G = np.array([p[1:4] for p in pts])
    fig, ax = plt.subplots(figsize=(11, 6.2))
    ax.axhspan(-1, 1, color="#f0efec", zorder=0)
    ax.text(T.min() - 0.5, 0.75, "test plan limit: within 1 °/s", color=INK2, fontsize=11, va="top")
    for k, (lab, c) in enumerate((("x", BLUE), ("y", ORANGE), ("z", AQUA))):
        ax.scatter(T, G[:, k], s=46, color=c, edgecolor=SURFACE, linewidth=1.2, zorder=3, label=f"gyro {lab}")
    # no straight-line fit: the drift flattens and eases back above about 66 C
    i_lo, i_hi = int(np.argmin(T)), int(np.argmin(G[:, 0]))
    ax.annotate(f"x axis: {G[i_lo, 0]:+.1f} °/s at {T[i_lo]:.0f} °C to {G[i_hi, 0]:+.1f} °/s at {T[i_hi]:.0f} °C,\n"
                "easing back above 70 °C (outdoors)", (T[i_lo], G[i_lo, 0]), (14, -46), textcoords="offset points",
                color=INK, fontsize=12)
    slope = (G[i_hi, 0] - G[i_lo, 0]) / (T[i_hi] - T[i_lo])
    ax.scatter([56], [-2.8], s=70, marker="D", facecolor=SURFACE, edgecolor=BLUE, linewidth=2, zorder=4)
    ax.annotate("9/27 bench run", (56, -2.8), (8, 8), textcoords="offset points", color=INK2, fontsize=11)
    ax.set(xlabel="sensor core temperature (°C)", ylabel="gyro reading at rest (°/s)")
    ax.legend(loc="lower left", frameon=False, ncol=3)
    finish(fig, [ax], "gyro_bias_vs_temp.png",
           f"Gyro bias at rest moves with temperature ({len(pts)} recordings, sensor stationary)")
    return slope


# ---------------------------------------------------------------- daylight penalty
def body_per_frame(name, kw):
    r = runs.get(name)
    if r is None or not r["range_center_m"]:
        return None
    res = cone.analyze(RAW / f"{name}.npz", float(r["tape_m"]), HEIGHT, 180, kw["top_m"], kw["max_width_m"])
    if not res["found"]:
        return None
    body = res["h"][res["cone_idx"]] > 0.08  # the base's hit count depends on cone rotation and floor, not light
    return float(res["range_center_m"]), body.sum() / (res["duration_s"] / 0.1)


def daylight():
    tapes = ["2p54", "3p81", "5p08", "6p35", "7p62", "8p89", "10p16", "11p43"]
    small, large, dusk_small = [], [], None
    for t in tapes:
        g = body_per_frame(f"small_{t}m_level", SMALL)
        outs = [body_per_frame(f"small_out_{t}m_level" + s, SMALL) for s in ("", "_run1", "_run2", "_run3")]
        outs = [o for o in outs if o]
        if g and outs:
            small.append((g[0], 100 * (np.mean([o[1] for o in outs]) / g[1] - 1)))
        if t == "10p16":
            d4 = body_per_frame("small_out_10p16m_level_run4", SMALL)
            dusk_small = (d4[0], 100 * (d4[1] / g[1] - 1)) if d4 and g else None
        if t != "11p43":  # the garage run at 11.5 m lost the cone's top to the garage position, not the light
            gl = body_per_frame(f"cone_{t}m_level", LARGE)
            ol = body_per_frame(f"large_out_{t}m_level", LARGE)
            if gl and ol:
                large.append((gl[0], 100 * (ol[1] / gl[1] - 1)))
    fig, ax = plt.subplots(figsize=(11, 6.2))
    ax.axhline(0, color=INK2, lw=1)
    for series, c, lab in ((small, ORANGE, "small cone, overcast daylight (16:46-17:00)"),
                           (large, BLUE, "large cone, dusk (17:14-17:27)")):
        x, y = zip(*series)
        ax.plot(x, y, color=c, lw=2, marker="o", ms=9, mec=SURFACE, mew=1.5, zorder=3, label=lab)
    if dusk_small:
        ax.plot(*dusk_small, "o", ms=9, mfc=SURFACE, mec=ORANGE, mew=2, zorder=3)
        ax.annotate("same 10.3 m spot, 6 min later\nas the light faded",
                    dusk_small, (12.25, -36), textcoords="data", color=INK2, fontsize=11, va="center", ha="right",
                    arrowprops=dict(arrowstyle="-", color=MUTED))
    ax.text(12.2, 1.5, "same as the garage", color=INK2, fontsize=11, va="bottom", ha="right")
    ax.set(xlabel="range to cone, measured by the sensor (m)", ylabel="cone-body hits outdoors vs garage (%)",
           xlim=(2, 12.3), ylim=(-75, 25))
    ax.legend(loc="lower left", frameon=False)
    finish(fig, [ax], "daylight_penalty.png", "Daylight costs hits at range, and more light costs more")


# ---------------------------------------------------------------- mount height and the near-field blind zone
def mount_height():
    # effective lower field-of-view edge from the runs where it cut the cone: angle below the
    # horizon to the lowest hit, corrected by the measured pitch toward the cone (all cone-side low,
    # except the first 5 m try, which was 2.5 deg cone-side high)
    angs = []
    for name, r in runs.items():
        if not r["range_center_m"] or float(r["visible_low_cm"]) < 8 or r["tilt_setting"] != "level":
            continue
        rng, low = float(r["range_center_m"]), float(r["visible_low_cm"]) / 100
        pitch_low = -float(r["imu_tilt_deg"]) if name.endswith("_try1") else float(r["imu_tilt_deg"])
        angs.append(math.degrees(math.atan((HEIGHT - low) / rng)) - pitch_low)
    alpha = float(np.median(angs))
    h = np.linspace(0.2, 1.2, 101)
    fig, ax = plt.subplots(figsize=(11, 6.2))
    # the garage runs had the sensor tipped slightly toward the cones; draw that pitch too so the
    # measured points have their own curve
    tested = float(np.median([float(r["imu_tilt_deg"]) for n, r in runs.items()
                              if n.startswith("small_") and not n.startswith("small_out")]))
    for pitch, ls, lw in ((0, "-", 2), (tested, ":", 1.6), (3, "--", 2)):
        a = math.radians(alpha + pitch)
        full = h / math.tan(a)
        top = np.clip(h - 0.325, 0, None) / math.tan(a)
        lab = "level" if pitch == 0 else (f"as tested, {pitch:.1f}° down" if pitch == tested else f"pitched {pitch}° down")
        ax.plot(h, full, color=BLUE, lw=lw, ls=ls, label=f"whole small cone in view, {lab}")
        ax.plot(h, top, color=ORANGE, lw=lw, ls=ls, label=f"top of small cone in view, {lab}")
    meas = [(1.43, "not visible", CRIT), (2.65, "top 17 cm", ORANGE), (3.88, "whole cone", BLUE)]
    for rng, lab, c in meas:
        ax.plot(HEIGHT, rng, "o", ms=10, color=c, mec=SURFACE, mew=1.5, zorder=4)
        ax.annotate(f"measured: {lab} at {rng:.2f} m", (HEIGHT, rng), (12, 0), textcoords="offset points",
                    va="center", color=INK, fontsize=11, bbox=dict(fc=SURFACE, ec="none", pad=1.5))
    ax.axvline(HEIGHT, color=MUTED, lw=1, ls=":")
    ax.text(HEIGHT, 9.6, " this test, 0.58 m", color=INK2, fontsize=11, va="top")
    ax.set(xlabel="LiDAR mount height above ground (m)", ylabel="closest range the cone is seen (m)", xlim=(0.2, 1.2), ylim=(0, 10))
    ax.legend(loc="upper left", frameon=False, fontsize=11)
    finish(fig, [ax], "mount_height.png",
           f"Near-field blind zone vs mount height (field of view floor fitted at {alpha:.1f}° below the sensor)")
    return alpha


# ---------------------------------------------------------------- frames to stack before a reliable detection
def frames_needed(per, need, conf=0.95, kmax=40):
    for k in range(1, kmax + 1):
        sums = np.convolve(per, np.ones(k, int), "valid")
        if len(sums) and (sums >= need).mean() >= conf:
            return k
    return None


def frames_to_detect():
    series = {"garage": [], "outdoors, overcast": []}
    for name, r in runs.items():
        if not r["range_center_m"] or not name.startswith("small"):
            continue
        if name.endswith("_run4"):  # the dusk repeat; the overcast runs carry this position
            continue
        res = cone.analyze(RAW / f"{name}.npz", float(r["tape_m"]), HEIGHT, 180, *SMALL.values())
        if not res["found"]:
            continue
        key = "outdoors, overcast" if name.startswith("small_out") else "garage"
        series[key].append((float(r["range_center_m"]), res["per_frame"]))
    fig, ax = plt.subplots(figsize=(11, 6.2))
    for (key, c), need, ls in [((k, c), n, ls) for n, ls in ((3, "-"), (1, ":")) for k, c in (("garage", BLUE), ("outdoors, overcast", ORANGE))]:
        by_rng = {}
        for rng, per in series[key]:
            by_rng.setdefault(round(rng, 1), []).append(per)
        xs, ys, capped = [], [], []
        for rng in sorted(by_rng):
            per = np.concatenate(by_rng[rng])
            k = frames_needed(per, need)
            if k is None:
                capped.append(rng)
            else:
                xs.append(rng)
                ys.append(k * 0.1)
        ax.plot(xs, ys, color=c, lw=2, ls=ls, marker="o" if need == 3 else None, ms=8, mec=SURFACE, mew=1.5, zorder=3,
                label=f"{key}, cluster of {need}+ point{'s' if need > 1 else ''}")
        for rng in capped:
            ax.plot(rng, 4.0, "^", ms=10, color=c, zorder=3)
            ax.annotate("not reached in 4 s", (rng, 4.0), (-8, 6), textcoords="offset points", ha="right",
                        color=INK2, fontsize=10)
    ax.text(18.3, 0.12, "outdoor runs past 12 m: light fading", color=INK2, fontsize=10, ha="right")
    ax.axhline(0.5, color=INK2, lw=1, ls="--", zorder=1)
    ax.text(0.3, 0.55, "0.5 s: the car covers 5.6 m at the 40 km/h demo cap", color=INK2, fontsize=11, va="bottom")
    ax.set(xlabel="range to small cone, measured by the sensor (m)", ylabel="stacking needed for 95% detection (s)",
           xlim=(0, 18.5), ylim=(0, 4.4))
    ax.legend(loc="upper left", frameon=False, fontsize=11)
    finish(fig, [ax], "frames_to_detect.png", "How long the LiDAR must look before a small cone is there 95% of the time")


# ---------------------------------------------------------------- session timeline
def timeline(hl):
    t = np.array([h[0] for h in hl])
    pts = np.array([h[1] for h in hl], float) / 1000
    tc = np.array([h[2] for h in hl], float)
    tc[pts == 0] = np.nan  # with no data the status poll fails and the log repeats a stale temperature
    # outages straight from the log: short ones are the USB adapter, long ones are power events
    outages, start = [], None
    for x, p in zip(t, pts):
        if p == 0 and start is None:
            start = x
        elif p > 0 and start is not None:
            outages.append((start, (x - start).total_seconds()))
            start = None
    # break the lines across stretches with no log (streamer stopped or Mac asleep)
    gaps = np.flatnonzero(np.diff([x.timestamp() for x in t]) > 10)
    tt, pp, cc = list(t), list(pts), list(tc)
    for i in gaps[::-1]:
        tt.insert(i + 1, t[i] + dt.timedelta(seconds=1)); pp.insert(i + 1, np.nan); cc.insert(i + 1, np.nan)
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(14, 7.5), sharex=True, gridspec_kw=dict(height_ratios=[1, 1]))
    out_from = dt.datetime(2026, 10, 4, 16, 38, 34)  # first data after the move outside
    for ax in (a1, a2):
        ax.axvspan(out_from, t[-1], color="#f0efec", zorder=0)
    a1.plot(tt, pp, color=BLUE, lw=1.2)
    a1.set(ylabel="points per second (thousands)", ylim=(-10, 240))
    a1.text(out_from, 225, "  outdoors", color=INK2, fontsize=12, va="top")
    a1.text(t[0], 225, "garage", color=INK2, fontsize=12, va="top")
    a2.plot(tt, cc, color=ORANGE, lw=1.6)
    a2.set(ylabel="core temperature (°C)", ylim=(50, 76))
    long_labels = {"15:14": "moved, power cycled\n(Mac slept 15:16)", "16:29": "moved outside,\npower cycled",
                   "17:01": "bench supply\ncut out"}
    n_short = 0
    for i, (x, secs) in enumerate(outages):
        if secs <= 15:
            n_short += 1
            a1.axvline(x, color=MUTED, lw=1, ls=":", zorder=1)
        else:
            a1.axvline(x, color=CRIT, lw=1.2, ls=":", zorder=1)
            a1.annotate(long_labels.get(x.strftime("%H:%M"), f"{secs:.0f} s outage"), (x, 60 + 45 * (i % 2)), (5, 0),
                        textcoords="offset points", color=INK, fontsize=10.5, va="center")
    a1.annotate(f"grey: {n_short} USB adapter dropouts, 5 s each", (t[0], 182), (4, 0), textcoords="offset points",
                color=INK2, fontsize=10.5, va="top", bbox=dict(fc=SURFACE, ec="none", pad=1))
    import matplotlib.dates as mdates
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    a2.set(xlabel="time, 2026-10-04 (CDT)")
    finish(fig, [a1, a2], "session_timeline.png", "The session: steady 200k points/s between host-side outages; heat climbs outdoors")


if __name__ == "__main__":
    hl = health()
    print("gyro x drift, deg/s per deg C from the coolest run to the most negative", gyro_vs_temp(hl))
    daylight()
    print("fitted FOV floor deg", mount_height())
    frames_to_detect()
    timeline(hl)
