# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Top-down and side views of a capture, for a quick look at what the sensor sees."""
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src, out = sys.argv[1], sys.argv[2]
lim = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
d = np.load(src)
xyz, r, t = d["xyz"], d["refl"], d["t"]
ok = np.linalg.norm(xyz, axis=1) > 0.05
sel = ok & (t - t.min() < 1.0)  # one second accumulated
p, rr = xyz[sel], r[sel]
fig, ax = plt.subplots(1, 2, figsize=(16, 8), gridspec_kw=dict(width_ratios=[1, 1]))
ax[0].scatter(p[:, 0], p[:, 1], c=p[:, 2], s=0.3, cmap="viridis", vmin=-1.0, vmax=2.0)
for rad in (2, 5, 10, 15, 20):
    if rad <= lim * 1.5:
        ax[0].add_patch(plt.Circle((0, 0), rad, fill=False, ls=":", lw=0.6, color="gray"))
        ax[0].text(rad * 0.707, rad * 0.707, f"{rad} m", fontsize=8, color="gray")
ax[0].arrow(0, 0, 1.0, 0, width=0.05, color="red")
ax[0].set(xlim=(-lim, lim), ylim=(-lim, lim), aspect="equal", title="top down, 1 s, color = height (m); red arrow = sensor +x", xlabel="x (m)", ylabel="y (m)")
ax[1].scatter(p[:, 0], p[:, 2], c=rr, s=0.3, cmap="magma", vmin=0, vmax=150)
ax[1].set(xlim=(-lim, lim), ylim=(-2, 4), aspect="equal", title="side view x-z, color = reflectivity", xlabel="x (m)", ylabel="z (m)")
for a in ax:
    a.grid(alpha=0.2)
fig.tight_layout()
fig.savefig(out, dpi=110)
print("wrote", out, "points", len(p))
