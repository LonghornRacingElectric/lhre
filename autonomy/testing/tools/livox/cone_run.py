# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib"]
# ///
"""One cone-test position: record, analyze, save figures, and log the row.

Reads the campaign's session.json (cone, sensor height, tape reference, location, lighting) so
each position only needs its distance and tilt. Raw files land in <campaign>/raw/, figures in
<campaign>/figures/, and one row per run in <campaign>/runs.csv (a re-run replaces its row).

  uv run cone_run.py --dist 10 --tilt level [--note "..."] [--no-record]
"""
import argparse, csv, json, pathlib, re, shutil, time

import numpy as np

import cone

HERE = pathlib.Path(__file__).resolve().parent
CAMPAIGN = pathlib.Path.home() / "lhr-test-data" / "current"

COLS = ["run", "recorded", "tape_m", "tilt_setting", "cone", "sensor_height_m", "tape_reference", "location", "lighting",
        "imu_tilt_deg", "range_center_m", "near_face_m", "range_bias_cm", "bearing_deg", "width_cm", "visible_low_cm",
        "visible_high_cm", "fov_floor_at_cone_cm", "pts_total", "duration_s", "pts_per_frame", "median_per_frame",
        "frames_hit_pct", "pts_first5s", "pass_10m", "lost_packets", "blocked_returns", "note", "raw_mcap", "raw_npz"]


def record(name, secs):
    log = HERE / "live.log"
    done = f"REC done {name}:"
    before = log.read_text().count(done)
    (HERE / "rec.txt").write_text(f"{name} {secs}")
    deadline = time.time() + secs + 30
    while time.time() < deadline and log.read_text().count(done) <= before:
        time.sleep(0.5)
    line = [l for l in log.read_text().splitlines() if l.startswith(done)][-1]
    npz = HERE / "bags" / f"{name}.npz"
    while time.time() < deadline:  # the streamer writes the npz on a thread after "REC done"
        try:
            with np.load(npz) as d:
                d["imu_t"]
            break
        except Exception:
            time.sleep(0.5)
    return int(re.search(r"lost (\d+) packets", line).group(1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=float, required=True, help="tape distance, sensor center to cone center, m")
    ap.add_argument("--tilt", default="level", help="level, down3, ...")
    ap.add_argument("--note", default="")
    ap.add_argument("--secs", type=float, default=10)
    ap.add_argument("--no-record", action="store_true", help="re-analyze the existing raw files")
    ap.add_argument("--lost", type=int, default=None, help="packets lost, when re-analyzing a run made before logging")
    a = ap.parse_args()

    s = json.loads((CAMPAIGN / "session.json").read_text())
    name = f"{s.get('run_prefix', 'cone')}_{f'{a.dist:g}'.replace('.', 'p').zfill(2)}m_{a.tilt}"
    raw, figs = CAMPAIGN / "raw", CAMPAIGN / "figures"
    raw.mkdir(exist_ok=True)
    figs.mkdir(exist_ok=True)
    runs = CAMPAIGN / "runs.csv"
    rows = list(csv.DictReader(runs.open())) if runs.exists() else []
    prev = next((r for r in rows if r["run"] == name), None)

    if a.no_record:
        lost = a.lost if a.lost is not None else (int(prev["lost_packets"]) if prev else -1)
    else:
        print(f"recording {name} for {a.secs:.0f} s ...", flush=True)
        lost = record(name, a.secs)
        for ext in ("mcap", "npz"):
            shutil.move(HERE / "bags" / f"{name}.{ext}", raw / f"{name}.{ext}")

    res = cone.analyze(raw / f"{name}.npz", a.dist, s["sensor_height_m"], s.get("bearing_deg"),
                       s.get("cone_top_m"), s.get("cone_max_width_m"))
    cone.report(res)
    print(f"packets lost during the recording: {lost}")
    tilt_txt = "level" if a.tilt == "level" else a.tilt.replace("down", "tilted ") + " deg down"
    if res["found"]:  # label with the sensor's range; the tape reference was unreliable
        label = f"{s.get('cone_label', 'Cone')} at {res['range_center_m']:.2f} m, {tilt_txt}"
        cone.figures(res, str(figs / name), label, s.get("cone_dims_m"))
        print(f"figures: {figs / name}_overview.png, {figs / name}_silhouette.png")

    row = {k: "" for k in COLS}
    stamp = prev["recorded"] if (a.no_record and prev) else time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime((raw / f"{name}.mcap").stat().st_mtime))
    row.update(run=name, recorded=stamp,
               tape_m=a.dist, tilt_setting=a.tilt, cone=s["cone"], sensor_height_m=s["sensor_height_m"],
               tape_reference=s["tape_reference"], location=s["location"], lighting=s["lighting"], lost_packets=lost,
               note=a.note or (prev["note"] if prev else ""), raw_mcap=f"raw/{name}.mcap", raw_npz=f"raw/{name}.npz")
    row.update({k: res[k] for k in COLS if k in res and k not in ("note",)})
    if res["found"] and abs(res["range_center_m"] - 10) < 0.5:  # the plan's pass line is set at 10 m
        row["pass_10m"] = "PASS" if res["pts_first5s"] >= 50 else "FAIL"
    rows = [r for r in rows if r["run"] != name] + [row]
    rows.sort(key=lambda r: (r["tilt_setting"], float(r["tape_m"])))
    with runs.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"logged to {runs}")


if __name__ == "__main__":
    main()
