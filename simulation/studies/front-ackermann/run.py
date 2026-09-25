import copy
import csv
import json
import math
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.optimize import brentq, least_squares, minimize_scalar

from _0_Utils.kin_py.kinematics import CornerKinematics
from _0_Utils.vehicle_io import load_yaml, parse_tir, tire_templates_root
from _2_EnvelopeSim.vehicle_yaml import project_vehicle_yaml
from _5_App.tire_eval import _mf52_fx_pure, _mf52_fy_pure

G = 9.80665
MU_SCALE = 0.623
RADII_M = (3.5, 4.5, 6.0, 8.0)
ACKERMANN_PCT = (-50, -25, 0, 25, 50, 75, 100)
LKY_CASES = {"lky_1": 1.0, "lky_scaled": MU_SCALE}
SLIP_SIDES = {"pos": 1.0, "neg": -1.0}
LLTD_OFFSETS = (-0.20, 0.0, 0.10)
NOMINAL = ("lky_1", "pos", 0.0)
GRIP_PROBE = 1.01
DRAG_FRACTION = 0.8
RACK_MM = np.arange(0.0, 60.5, 0.5)
REPORT_RACK_MM = (10.0, 20.0, 30.0)
TIE_O_TARGETS_PCT = (0, 50, 100)
BUMP_MM = 25.0

FRONT_V19_M = {
    "lower_fore_i_m": [0.161431, 0.218700, 0.075838],
    "lower_aft_i_m": [-0.081547, 0.218700, 0.075838],
    "upper_fore_i_m": [0.165945, 0.269958, 0.170429],
    "upper_aft_i_m": [-0.084020, 0.269958, 0.170429],
    "lower_o_m": [-0.002579, 0.586039, 0.123663],
    "upper_o_m": [0.010194, 0.568166, 0.274234],
    "tie_o_m": [0.085781, 0.570821, 0.199930],
    "wheel_center_m": [0.0, 0.622300, 0.199930],
}
RACK_PICKUP_V19_M = [0.050036, 0.245768, 0.121550]

RADIUS_COLORS = ("#0d366b", "#1c5cab", "#3987e5", "#86b6ef")
CAR_COLORS = {"Front v19": "#2a78d6", "Orion": "#eb6834"}
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


class Car:
    def __init__(self, ggv, tires, lltd):
        self.mass, self.wheelbase, self.cg_height = ggv.mass, ggv.wheelbase, ggv.cg_height
        self.track_front, self.track_rear = ggv.track_front, ggv.track_rear
        self.front_frac, self.lltd, self.tires = ggv.front_static_frac, lltd, tires
        a = self.wheelbase * (1.0 - self.front_frac)
        b = self.wheelbase * self.front_frac
        self.corners = (
            (a, self.track_front / 2), (a, -self.track_front / 2),
            (-b, self.track_rear / 2), (-b, -self.track_rear / 2),
        )

    def loads(self, ay_g):
        front = self.mass * G * self.front_frac / 2
        rear = self.mass * G * (1.0 - self.front_frac) / 2
        moment = self.mass * ay_g * G * self.cg_height
        df = self.lltd * moment / self.track_front
        dr = (1.0 - self.lltd) * moment / self.track_rear
        return (max(front - df, 0.0), front + df, max(rear - dr, 0.0), rear + dr)

    def with_grip(self, front=1.0, rear=1.0):
        probe = copy.copy(self)
        f = {**self.tires[0], "LMUY": self.tires[0]["LMUY"] * front}
        r = {**self.tires[2], "LMUY": self.tires[2]["LMUY"] * rear}
        probe.tires = (f, f, r, r)
        return probe


def with_front_v19(vehicle, tie_o_dy_m=0.0):
    v19 = copy.deepcopy(vehicle)
    v19["front"]["suspension"].update(copy.deepcopy(FRONT_V19_M))
    v19["front"]["suspension"]["tie_o_m"][1] += tie_o_dy_m
    v19["front"]["steering"]["rack_pickup_m"] = list(RACK_PICKUP_V19_M)
    return v19


def steer_table(vehicle, rack_mm=RACK_MM):
    corner = CornerKinematics.from_vehicle(vehicle, "front")
    toe = {}
    for sign in (1.0, -1.0):
        guess = np.zeros(3)
        for rack in rack_mm:
            try:
                x, points, residual = corner.solve_jounce(0.0, guess, rack_displacement_m=sign * rack / 1000.0)
            except ValueError:
                break
            toe[sign * rack] = math.radians(corner.curve_values(points, x, residual)["toe_deg"])
            guess = x
    rows = [(r, toe[r], -toe[-r]) for r in rack_mm if r in toe and -r in toe]
    return np.array(rows), corner


def bump_toe_deg(corner, jounce_mm):
    x, points, residual = corner.solve_jounce(jounce_mm / 1000.0, np.zeros(3))
    return corner.curve_values(points, x, residual)["toe_deg"]


def ackermann_pct(inner, outer, track, wheelbase):
    return 100.0 * (1.0 / np.tan(outer) - 1.0 / np.tan(inner)) * wheelbase / track


def at_rack(table, rack_mm, column):
    return float(np.interp(rack_mm, table[:, 0], table[:, column]))


def constant_curve(pct, track, wheelbase):
    k = pct / 100.0 * track / wheelbase
    return lambda outer: math.atan2(math.tan(outer), 1.0 - k * math.tan(outer))


def table_curve(table):
    return lambda outer: float(np.interp(outer, table[:, 2], table[:, 1]))


def lateral_n(tire, fz, alpha, side):
    return -side * _mf52_fy_pure(tire, fz, side * alpha, 0.0)


def peak_lateral_n(tire, fz, side):
    res = minimize_scalar(
        lambda a: -lateral_n(tire, fz, a, side), bounds=(0.0, 0.6), method="bounded", options={"xatol": 1e-7},
    )
    return -res.fun


def state(car, curve, radius, side, beta, outer, ay_g):
    speed = math.sqrt(ay_g * G * radius)
    yaw_rate = speed / radius
    steer = (curve(outer), outer, 0.0, 0.0)
    loads = car.loads(ay_g)
    fx = fy = mz = 0.0
    alphas, forces = [], []
    for (x, y), delta, fz, tire in zip(car.corners, steer, loads, car.tires):
        alpha = delta - math.atan2(speed * math.sin(beta) + yaw_rate * x, speed * math.cos(beta) - yaw_rate * y)
        force = lateral_n(tire, fz, alpha, side)
        fx -= force * math.sin(delta)
        fy += force * math.cos(delta)
        mz += x * force * math.cos(delta) + y * force * math.sin(delta)
        alphas.append(alpha)
        forces.append(force)
    drive = -car.mass * ay_g * G * math.sin(beta) - fx
    return {
        "speed": speed, "steer": steer, "loads": loads, "alphas": alphas, "forces": forces,
        "fy": fy, "mz": mz, "drive_n": drive,
    }


def balance(car, curve, radius, side, beta, outer, ay_g):
    s = state(car, curve, radius, side, beta, outer, ay_g)
    weight = car.mass * G
    return [(s["fy"] - weight * ay_g * math.cos(beta)) / weight, s["mz"] / (weight * car.wheelbase)]


def axle_slopes(car, curve, radius, side, beta, outer, ay_g, eps=1e-4):
    s = state(car, curve, radius, side, beta, outer, ay_g)
    rise = [
        (lateral_n(t, fz, a + eps, side) - f) / eps
        for t, fz, a, f in zip(car.tires, s["loads"], s["alphas"], s["forces"])
    ]
    weight = car.mass * G
    return [(rise[0] + rise[1]) / weight, (rise[2] + rise[3]) / weight]


def is_stable(car, curve, radius, side, beta, outer, ay_g):
    return min(axle_slopes(car, curve, radius, side, beta, outer, ay_g)) >= -1e-6


def trim(car, curve, radius, side, ay_g, outer_max, guess=None):
    rear_path = car.wheelbase * car.front_frac / radius
    outer_path = math.atan(car.wheelbase / (radius + car.track_front / 2))
    guesses = [guess] if guess is not None else [
        (rear_path - db, min(max(outer_path + ds, 0.0), outer_max))
        for db in (0.0, 0.03, 0.06) for ds in (0.0, 0.05, -0.05, 0.10)
    ]
    for start in guesses:
        res = least_squares(
            lambda z: balance(car, curve, radius, side, z[0], z[1], ay_g),
            start, bounds=([-0.4, 0.0], [0.4, outer_max]), xtol=1e-12, ftol=1e-12, gtol=1e-12,
        )
        if max(abs(v) for v in res.fun) < 1e-7 and is_stable(car, curve, radius, side, *res.x, ay_g):
            return res.x
    return None


def limit_ay(car, curve, radius, side, outer_max, low=0.1, high=2.5, tol=2e-5):
    best = trim(car, curve, radius, side, low, outer_max)
    if best is None:
        return None
    while high - low > tol:
        mid = 0.5 * (low + high)
        z = trim(car, curve, radius, side, mid, outer_max, best)
        if z is None:
            z = trim(car, curve, radius, side, mid, outer_max)
        if z is None:
            high = mid
        else:
            low, best = mid, z
    return best[0], best[1], low


def probe_gain(car, curve, radius, side, outer_max, ay_g):
    limit = limit_ay(car, curve, radius, side, outer_max)
    return float("nan") if limit is None else limit[2] / ay_g - 1.0


def exit_accel_g(car, max_drive_force):
    fz = car.mass * G * (1.0 - car.front_frac) / 2
    mu = max(abs(_mf52_fx_pure(car.tires[2], fz, k, 0.0)) for k in np.linspace(0.0, 0.4, 401)) / fz
    traction = mu * (1.0 - car.front_frac) / (1.0 - mu * car.cg_height / car.wheelbase)
    return min(traction, max_drive_force / (car.mass * G))


def turn_time_delta_s(radius, ay_ref_g, ay_g, exit_g):
    v_ref, v = math.sqrt(ay_ref_g * G * radius), math.sqrt(ay_g * G * radius)
    return math.pi * radius * (1.0 / v - 1.0 / v_ref) - (v - v_ref) / (exit_g * G)


def tie_o_shift(vehicle, target_pct, track, wheelbase):
    rack = np.arange(0.0, REPORT_RACK_MM[1] + 0.5, 0.5)

    def error(dy_mm):
        table, _ = steer_table(with_front_v19(vehicle, dy_mm / 1000.0), rack)
        return ackermann_pct(table[-1, 1], table[-1, 2], track, wheelbase) - target_pct

    try:
        dy = brentq(error, -30.0, 60.0, xtol=0.01)
    except ValueError:
        return None
    table, corner = steer_table(with_front_v19(vehicle, dy / 1000.0))
    return {
        "target_pct_at_20mm_rack": target_pct,
        "tie_o_outboard_shift_mm": round(dy, 2),
        "ackermann_pct": {f"{r:g}mm": round(float(ackermann_pct(at_rack(table, r, 1), at_rack(table, r, 2), track, wheelbase)), 1) for r in REPORT_RACK_MM},
        "mean_steer_deg_at_20mm": round(math.degrees(0.5 * (at_rack(table, 20.0, 1) + at_rack(table, 20.0, 2))), 2),
        "bump_toe_deg": {f"{j:+g}mm": round(bump_toe_deg(corner, j), 3) for j in (-BUMP_MM, BUMP_MM)},
    }


def solve_cases(ggv, tir, curves, outer_max):
    rows = []
    for lky_name, lky in LKY_CASES.items():
        tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": lky}
        for side_name, side in SLIP_SIDES.items():
            for lltd_offset in LLTD_OFFSETS:
                car = Car(ggv, (tire,) * 4, ggv.lltd + lltd_offset)
                exit_g = exit_accel_g(car, ggv.max_drive_force)
                for radius in RADII_M:
                    ref_ay = None
                    for name, curve in curves.items():
                        case = {
                            "tire_case": lky_name, "slip_side": side_name, "lltd_front": round(car.lltd, 4),
                            "lltd_offset": lltd_offset, "radius_m": radius, "curve": name,
                        }
                        limit = limit_ay(car, curve, radius, side, outer_max)
                        if limit is None:
                            rows.append({**case, "ay_max_g": float("nan"), "ay_change_pct": float("nan")})
                            continue
                        beta, outer, ay = limit
                        s = state(car, curve, radius, side, beta, outer, ay)
                        if ref_ay is None:
                            ref_ay = ay
                        front_gain = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, side, outer_max, ay)
                        rear_gain = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, side, outer_max, ay)
                        peaks = [peak_lateral_n(t, fz, side) for t, fz in zip(car.tires, s["loads"])]
                        partial = trim(car, curve, radius, side, DRAG_FRACTION * ref_ay, outer_max)
                        drag = None
                        if partial is not None:
                            drag = state(car, curve, radius, side, *partial, DRAG_FRACTION * ref_ay)["drive_n"]
                        inner = s["steer"][0]
                        rows.append({
                            **case, "ay_max_g": ay, "ay_change_pct": 100.0 * (ay / ref_ay - 1.0),
                            "speed_mps": s["speed"], "beta_deg": math.degrees(beta),
                            "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
                            "ackermann_pct_at_limit": float(ackermann_pct(inner, outer, car.track_front, car.wheelbase)),
                            **{f"alpha_{c}_deg": math.degrees(a) for c, a in zip(("fl", "fr", "rl", "rr"), s["alphas"])},
                            **{f"fz_{c}_n": f for c, f in zip(("fl", "fr", "rl", "rr"), s["loads"])},
                            "front_grip_used": sum(s["forces"][:2]) / sum(peaks[:2]),
                            "rear_grip_used": sum(s["forces"][2:]) / sum(peaks[2:]),
                            "ay_gain_pct_per_1pct_front_grip": 100.0 * front_gain,
                            "ay_gain_pct_per_1pct_rear_grip": 100.0 * rear_gain,
                            "limiting_axle": "front" if np.nan_to_num(front_gain, nan=-1.0) > np.nan_to_num(rear_gain, nan=-1.0) else "rear",
                            "drive_n_at_80pct_ref_ay": drag,
                            "turn_180_delta_s": turn_time_delta_s(radius, ref_ay, ay, exit_g),
                            "exit_accel_g": exit_g,
                        })
    return rows


def is_nominal(row):
    return (row["tire_case"], row["slip_side"], row["lltd_offset"]) == NOMINAL


def plot_curves(path, tables, track_wheelbase, steer_band):
    fig, ax = plt.subplots(figsize=(6.4, 4.0), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axvspan(*steer_band, color="#e9e8e4", lw=0)
    ax.text(steer_band[0] + 0.3, 0.03, "mean steer at the limit\nR = 3.5 to 8 m", transform=ax.get_xaxis_transform(), va="bottom", fontsize=8, color=MUTED)
    for name, table in tables.items():
        keep = (table[:, 0] >= 2.0) & (np.degrees(0.5 * (table[:, 1] + table[:, 2])) <= 40.0)
        mean = np.degrees(0.5 * (table[keep, 1] + table[keep, 2]))
        pct = ackermann_pct(table[keep, 1], table[keep, 2], *track_wheelbase[name])
        ax.plot(mean, pct, color=CAR_COLORS[name], lw=2)
        ax.annotate(name, (mean[-1], pct[-1]), xytext=(4, 0), textcoords="offset points", va="center", fontsize=9, color=INK)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    ax.set_xlabel("Mean roadwheel steer (deg)", color=INK)
    ax.set_ylabel("Ackermann, cotangent convention (%)", color=INK)
    ax.set_title("Front steering geometry from hardpoints", color=INK, loc="left", fontsize=11)
    style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_grip(path, rows, v19_pct):
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.0, 4.2), facecolor=SURFACE)
    pct = np.array(ACKERMANN_PCT, dtype=float)
    for radius, color in zip(RADII_M, RADIUS_COLORS):
        def series(key, select):
            picked = [next(r.get(key) for r in rows if select(r) and r["radius_m"] == radius and r["curve"] == f"{p:+d}%") for p in ACKERMANN_PCT]
            return np.array([np.nan if v is None else v for v in picked], dtype=float)

        nominal = series("ay_change_pct", is_nominal)
        cases = {(r["tire_case"], r["slip_side"], r["lltd_offset"]) for r in rows}
        spread = np.array([series("ay_change_pct", lambda r, c=c: (r["tire_case"], r["slip_side"], r["lltd_offset"]) == c) for c in cases])
        left.fill_between(pct, np.nanmin(spread, axis=0), np.nanmax(spread, axis=0), color=color, alpha=0.15, lw=0)
        left.plot(pct, nominal, color=color, lw=2, marker="o", ms=4, label=f"R = {radius:g} m")
        left.plot([v19_pct[radius]], [0.0], marker="D", ms=7, color=color, mec=SURFACE, mew=1.5)
        drag = series("drive_n_at_80pct_ref_ay", is_nominal)
        right.plot(pct, drag, color=color, lw=2, marker="o", ms=4, label=f"R = {radius:g} m")
    left.axhline(0.0, color=MUTED, lw=0.8)
    left.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    left.set_ylabel("Max lateral g change vs Front v19 (%)", color=INK)
    left.set_title("Grip in tight corners (diamond = Front v19)", color=INK, loc="left", fontsize=11)
    right.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    right.set_ylabel("Drive force to hold speed (N)", color=INK)
    right.set_title("Cornering drag at 80% of the Front v19 limit", color=INK, loc="left", fontsize=11)
    for ax in (left, right):
        style(ax)
    left.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(color="#e4e3df", lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)


def main():
    out = Path(os.environ["OUT_DIR"])
    orion = load_yaml(Path(os.environ["BOBSIM_VEHICLE"]))
    v19 = with_front_v19(orion)
    (out / "vehicle_front_v19.yml").write_text(yaml.safe_dump(v19, sort_keys=False), encoding="utf-8")

    projection = project_vehicle_yaml(v19)
    ggv = projection.ggv
    orion_ggv = project_vehicle_yaml(orion).ggv
    orion_table, _ = steer_table(orion)
    v19_table, corner = steer_table(v19)
    origin = corner.initial_point_set()

    with (out / "steering_curves.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["car", "rack_mm", "inner_deg", "outer_deg", "ackermann_pct"])
        for name, table, g in (("Orion", orion_table, orion_ggv), ("Front v19", v19_table, ggv)):
            for rack, inner, outer in table:
                pct = ackermann_pct(inner, outer, g.track_front, g.wheelbase) if rack >= 2.0 else float("nan")
                writer.writerow([name, rack, math.degrees(inner), math.degrees(outer), pct])

    curves = {"Front v19": table_curve(v19_table)}
    curves.update({f"{p:+d}%": constant_curve(p, ggv.track_front, ggv.wheelbase) for p in ACKERMANN_PCT})
    outer_max = float(v19_table[-1, 2])
    tir = parse_tir(tire_templates_root(v19) / f"{v19['front']['tire']['template']}.tir")
    rows = solve_cases(ggv, tir, curves, outer_max)

    with (out / "limits.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(max(rows, key=len)))
        writer.writeheader()
        writer.writerows(rows)

    v19_rows = [r for r in rows if r["curve"] == "Front v19"]
    nominal_v19 = {r["radius_m"]: r for r in v19_rows if is_nominal(r)}
    v19_pct = {radius: r["ackermann_pct_at_limit"] for radius, r in nominal_v19.items()}
    steer_band = (
        min(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_v19.values()),
        max(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_v19.values()),
    )
    tables = {"Orion": orion_table, "Front v19": v19_table}
    geometry = {"Orion": (orion_ggv.track_front, orion_ggv.wheelbase), "Front v19": (ggv.track_front, ggv.wheelbase)}
    plot_curves(out / "ackermann_curves.png", tables, geometry, steer_band)
    plot_grip(out / "grip_vs_ackermann.png", rows, v19_pct)

    tight = nominal_v19[RADII_M[0]]
    lock_rack_mm = float(np.interp(math.radians(tight["inner_deg"]), v19_table[:, 1], v19_table[:, 0]))
    table_rows = {}
    for radius in RADII_M:
        table_rows[f"{radius:g}m"] = {
            r["curve"]: {
                "ay_change_pct": round(r["ay_change_pct"], 2),
                "turn_180_delta_ms": round(1000.0 * r.get("turn_180_delta_s", float("nan")), 1),
                "drive_n_at_80pct": None if r.get("drive_n_at_80pct_ref_ay") is None else round(r["drive_n_at_80pct_ref_ay"], 1),
                "limiting_axle": r.get("limiting_axle"),
            }
            for r in rows if is_nominal(r) and r["radius_m"] == radius
        }
    spread = {}
    for radius in RADII_M:
        for p in ACKERMANN_PCT:
            values = [r["ay_change_pct"] for r in rows if r["radius_m"] == radius and r["curve"] == f"{p:+d}%"]
            spread.setdefault(f"{radius:g}m", {})[f"{p:+d}%"] = [round(float(np.nanmin(values)), 2), round(float(np.nanmax(values)), 2)]
    best = {}
    for key in {(r["tire_case"], r["slip_side"], r["lltd_offset"], r["radius_m"]) for r in rows}:
        subset = [r for r in rows if (r["tire_case"], r["slip_side"], r["lltd_offset"], r["radius_m"]) == key and r["curve"] != "Front v19"]
        best.setdefault(f"{key[3]:g}m", []).append(max(subset, key=lambda r: np.nan_to_num(r["ay_max_g"], nan=-1.0))["curve"])

    summary = {
        "vehicle": {
            "note": "Front v19 steering hardpoints on Orion mass, CG, rear, roll stiffness and tire",
            "mass_kg": round(ggv.mass, 2), "cg_height_m": round(ggv.cg_height, 4),
            "front_static_frac": round(ggv.front_static_frac, 4), "wheelbase_m": round(ggv.wheelbase, 4),
            "track_front_m": round(ggv.track_front, 4), "track_rear_m": round(ggv.track_rear, 4),
            "lltd_front": round(ggv.lltd, 4), "lltd_source": projection.summary.get("lltd_source"),
        },
        "tire": {"template": v19["front"]["tire"]["template"], "LMUY_LMUX": MU_SCALE, "LKY_cases": LKY_CASES},
        "front_v19_geometry": {
            "caster_deg": round(corner.caster_deg(origin), 2), "kpi_deg": round(corner.kpi_deg(origin), 2),
            "bump_toe_deg": {f"{j:+g}mm": round(bump_toe_deg(corner, j), 3) for j in (-BUMP_MM, BUMP_MM)},
        },
        "ackermann_pct_at_rack": {
            name: {f"{r:g}mm": round(float(ackermann_pct(at_rack(t, r, 1), at_rack(t, r, 2), *geometry[name])), 1) for r in REPORT_RACK_MM}
            for name, t in tables.items()
        },
        "lock_at_3p5m_limit": {
            "inner_deg": round(tight["inner_deg"], 2), "outer_deg": round(tight["outer_deg"], 2),
            "rack_mm": round(lock_rack_mm, 1), "rack_sweep_max_mm": float(v19_table[-1, 0]),
        },
        "exit_accel_g": {r["tire_case"]: round(r["exit_accel_g"], 3) for r in v19_rows},
        "nominal_case": dict(zip(("tire_case", "slip_side", "lltd_offset"), NOMINAL)),
        "nominal": table_rows,
        "ay_change_pct_range_all_cases": spread,
        "best_constant_curve_per_case": {k: sorted(v) for k, v in best.items()},
        "tie_o_shift_for_target": [tie_o_shift(orion, p, ggv.track_front, ggv.wheelbase) for p in TIE_O_TARGETS_PCT],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("ackermann_pct_at_rack", "front_v19_geometry", "lock_at_3p5m_limit", "best_constant_curve_per_case")}, indent=2))


if __name__ == "__main__":
    main()
