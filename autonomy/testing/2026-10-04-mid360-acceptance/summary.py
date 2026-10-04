# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib"]
# ///
"""Campaign summary figure from runs.csv: points on the cone per frame against the sensor's range,
garage and outdoors side by side. Repeat runs at one position plot as their mean with a min-max bar.

  uv run summary.py [campaign_dir]
"""
import csv, pathlib, sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

camp = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path.home() / "lhr-test-data" / "current")
rows = list(csv.DictReader((camp / "runs.csv").open()))

GROUPS = {  # run-name prefix -> (panel, cone); longest prefixes first
    "small_out_": ("out", "small"), "large_out_": ("out", "large"), "small_": ("garage", "small"), "cone_": ("garage", "large"),
}


def group(run):
    for pre, g in GROUPS.items():
        if run.startswith(pre):
            return g
    return None


def collect(metric):
    series = defaultdict(lambda: defaultdict(list))  # (panel, cone) -> tape -> [(range, value)]
    missing = defaultdict(list)
    for r in rows:
        g = group(r["run"])
        if g is None or r["run"].endswith("_try1") or r["tilt_setting"] != "level":
            continue
        if r["range_center_m"]:
            series[g][r["tape_m"]].append((float(r["range_center_m"]), float(r[metric])))
        else:
            missing[g].append(float(r["tape_m"]) + 0.15)  # the tape read ~15 cm short of the sensor
    return series, missing

SURFACE, INK, INK2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#a8a7a0"
COLOR = {"large": "#2a78d6", "small": "#eb6834"}
LABEL = {"large": "large cone, ~52 cm", "small": "small FSAE cone, 325 mm"}
plt.rcParams.update({"font.size": 13, "axes.titlesize": 14, "figure.dpi": 150, "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2, "text.color": INK})
titles = {"garage": "Garage (indoors)",
          "out": "Outdoors: small cone in overcast daylight (16:46-17:13),\nlarge cone at dusk (17:14-17:27)"}


def figure(metric, ylabel, suptitle, out, ref, ref_text, ref_x, missing_y):
    series, missing = collect(metric)
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.2), sharey=True, facecolor=SURFACE)
    xmax = max(rg for g in series.values() for pts in g.values() for rg, _ in pts) + 1.0
    ymax = max(v for g in series.values() for pts in g.values() for _, v in pts) * 1.12
    for ax, panel in zip(axes, ("garage", "out")):
        ax.set_facecolor(SURFACE)
        ax.axhline(ref, color=INK2, lw=1, ls="--", zorder=1)
        ax.text(ref_x, ref + ymax * 0.012, ref_text, color=INK2, fontsize=10, ha="left", va="bottom")
        for cone in ("large", "small"):
            pts = series.get((panel, cone))
            if not pts:
                continue
            xs, ys, lo, hi = [], [], [], []
            for tape in sorted(pts, key=float):
                v = pts[tape]
                xs.append(sum(p[0] for p in v) / len(v))
                ys.append(sum(p[1] for p in v) / len(v))
                lo.append(ys[-1] - min(p[1] for p in v))
                hi.append(max(p[1] for p in v) - ys[-1])
            ax.errorbar(xs, ys, yerr=[lo, hi], color=COLOR[cone], lw=2, marker="o", ms=8, mec=SURFACE, mew=1.5,
                    capsize=4, elinewidth=1.5, zorder=3, label=LABEL[cone])
            for xm in missing.get((panel, cone), []):
                ax.plot(xm, missing_y, "x", ms=10, mew=2.5, color=COLOR[cone], zorder=3)
                ax.annotate(f"{cone} cone not visible", (xm, missing_y), (10, 0), textcoords="offset points", va="center", color=INK2, fontsize=10)
        ax.set(xlim=(0, xmax), ylim=(0, ymax), xlabel="range to cone, measured by the sensor (m)")
        ax.set_title(titles[panel], loc="left")
        ax.grid(axis="y", color="#e6e5e0", lw=1)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    axes[0].set_ylabel(ylabel)
    axes[0].legend(loc="upper right", frameon=False, fontsize=12)
    fig.suptitle(suptitle, x=0.01, ha="left", fontsize=16)
    fig.tight_layout()
    fig.savefig(camp / "figures" / out, facecolor=SURFACE)
    plt.close(fig)
    print("wrote", camp / "figures" / out)


figure("pts_per_frame", "points on the cone per 0.1 s frame", "Mid-360, sensor 0.58 m up, level: points on one cone vs range",
       "summary_points_vs_range.png", 1.0, "pass line (1 per frame)", 0.3, 0.3)
figure("frames_hit_pct", "frames with at least one hit on the cone (%)",
       "Mid-360, sensor 0.58 m up, level: how often a single 0.1 s frame sees the cone",
       "summary_frames_hit_vs_range.png", 50.0, "half of all frames", 4.2, 3.0)
