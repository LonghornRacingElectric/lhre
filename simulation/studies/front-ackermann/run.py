import copy
import csv
import json
import math
import os
import types
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.optimize import brentq, least_squares, minimize_scalar

from _0_Utils.kin_py.kinematics import CornerKinematics
from _0_Utils.lap_sim.qss import GGVMap, _GGVSlice
from _0_Utils.lap_sim.racing_line import optimize_racing_line
from _0_Utils.lap_sim.track import TrackCorridor
from _0_Utils.vehicle_io import load_yaml, parse_tir, repo_root, tire_templates_root
from _2_EnvelopeSim.vehicle_yaml import project_vehicle_yaml
from _5_App.tire_eval import _mf52_fx_pure, _mf52_fy_pure
from tools.parallel import map_cases

G = 9.80665
MU_SCALE = 0.623
RADII_M = (3.5, 4.5, 6.0, 8.0, 15.0)
ACKERMANN_PCT = (-50, -25, 0, 25, 50, 75, 100)
LKY_CASES = {"lky_1": 1.0, "lky_scaled": MU_SCALE}
SLIP_SIDES = {"pos": 1.0, "neg": -1.0}
LLTD_OFFSETS = (-0.10, 0.0, 0.10)
NOMINAL = ("lky_1", "pos", 0.0)
GRIP_PROBE = 1.01
DRAG_FRACTION = 0.8
RACK_MM = np.arange(0.0, 60.5, 0.5)
REPORT_RACK_MM = (10.0, 20.0, 30.0)
TIE_O_TARGETS_PCT = (0, 50, 100)
BUMP_MM = 25.0
BRAKE_G = (0.0, 0.3, 0.5)
BRAKE_BIAS_EXTRA = 0.70
BRAKE_RADII_M = (3.5, 4.5)
BRAKE_CASES = (
    ("ellipse", "lky_1", 0.0), ("ellipse", "lky_1", 0.10),
    ("ellipse", "lky_scaled", 0.0), ("ellipse", "lky_scaled", 0.10),
    ("slip_norm", "lky_1", 0.0),
)
FX_MAX_FZ_N = np.arange(0.0, 2525.0, 25.0)
DRIVE_G = (0.0, 0.2, 0.4)
DRIVE_RADII_M = (3.5, 4.5)
DRIVE_MODELS = ("ellipse", "slip_norm")
TOE_OUT_DEG = (-1.0, -0.5, 0.0, 0.5, 1.0)
TOE_RADII_M = (3.5, 4.5)
TOE_ACKERMANN_PCT = tuple(range(0, 101, 10))
REGEN_BIAS = (0.84, 0.70)
MASS_CASES = (
    ("mass +10 kg", "mass", 10.0), ("mass -10 kg", "mass", -10.0),
    ("CG +20 mm", "cg_height", 0.020), ("CG -20 mm", "cg_height", -0.020),
    ("front +3 pts", "front_static_frac", 0.03), ("front -3 pts", "front_static_frac", -0.03),
    ("camber -1 deg", "camber", None),
)
LB_TO_KG = 0.45359237
TEAM_2027 = {"mass": (430.0 + 150.0) * LB_TO_KG, "cg_height": 11.0 * 0.0254, "front_static_frac": 0.46}
STATIC_CAMBER_DEG = 0.0
TEAM_CAMBER_DEG = -1.0
RACK_TRAVEL_MM = 31.75
BALANCE_RADIUS_M = 15.0
STEER_FIX_TARGETS_PCT = (0.0, 50.0, 70.0)
STEER_RESERVE = 0.9
PLANNED_DIFF = (5.0, 0.60, 0.35)
LAP_CONFIG = "_3_StandardSim/LapTimeEval/lap_time_eval_config.yml"
CORNER_RADIUS_MAX_M = 15.0
EXIT_ACCEL_LOW_G = 0.4
ENTRY_DECEL_LOW_G = 0.5
GGV_FIELDS = ("mass", "wheelbase", "cg_height", "track_front", "track_rear", "front_static_frac", "lltd", "max_drive_force")

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

RADIUS_COLORS = ("#0d366b", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef")
BRAKE_COLORS = ("#f09a70", "#eb6834", "#a8421a")
CAR_COLORS = {"Front v19": "#2a78d6", "Orion": "#eb6834"}
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


class Car:
    def __init__(self, ggv, tires, lltd, camber_deg=STATIC_CAMBER_DEG):
        self.mass, self.wheelbase, self.cg_height = ggv.mass, ggv.wheelbase, ggv.cg_height
        self.track_front, self.track_rear = ggv.track_front, ggv.track_rear
        self.front_frac, self.lltd, self.tires = ggv.front_static_frac, lltd, tires
        a = self.wheelbase * (1.0 - self.front_frac)
        b = self.wheelbase * self.front_frac
        self.corners = (
            (a, self.track_front / 2), (a, -self.track_front / 2),
            (-b, self.track_rear / 2), (-b, -self.track_rear / 2),
        )
        self.combined = "ellipse"
        c = math.radians(camber_deg)
        self.camber = (c, -c, c, -c)
        self.fx_max_n, self.kappa_peak, self.alpha_peak = {-1: [], 1: []}, {-1: [], 1: []}, []
        for t in (tires[0], tires[2]):
            for sign, bounds in ((-1, (-0.5, 0.0)), (1, (0.0, 0.5))):
                fx = [minimize_scalar(lambda k: -abs(_mf52_fx_pure(t, fz, k, 0.0)), bounds=bounds, method="bounded") for fz in FX_MAX_FZ_N]
                self.fx_max_n[sign].append(np.array([abs(r.fun) for r in fx]))
                self.kappa_peak[sign].append(np.array([abs(r.x) for r in fx]))
            fy = [minimize_scalar(lambda a: -lateral_n(t, fz, a, 1.0), bounds=(0.0, 0.6), method="bounded") for fz in FX_MAX_FZ_N]
            self.alpha_peak.append(np.array([r.x for r in fy]))

    def loads(self, ay_g, decel_g=0.0):
        front = self.mass * G * self.front_frac / 2
        rear = self.mass * G * (1.0 - self.front_frac) / 2
        moment = self.mass * ay_g * G * self.cg_height
        df = self.lltd * moment / self.track_front
        dr = (1.0 - self.lltd) * moment / self.track_rear
        dx = self.mass * decel_g * G * self.cg_height / self.wheelbase / 2
        return (max(front - df + dx, 0.0), front + df + dx, max(rear - dr - dx, 0.0), rear + dr - dx)

    def peak(self, table, corner, fz):
        return float(np.interp(fz, FX_MAX_FZ_N, table[corner // 2]))

    def lateral(self, corner, fz, alpha, fx_w, side):
        tire = self.tires[corner]
        if fz <= 1e-3:
            return 0.0, (math.inf if fx_w else 0.0)
        gamma = self.camber[corner]
        if fx_w == 0.0:
            return lateral_n(tire, fz, alpha, side, gamma), 0.0
        sign = 1 if fx_w > 0.0 else -1
        if self.combined == "ellipse":
            limit = self.peak(self.fx_max_n[sign], corner, fz)
            use = abs(fx_w) / limit if limit > 0.0 else math.inf
            return lateral_n(tire, fz, alpha, side, gamma) * math.sqrt(max(0.0, 1.0 - use * use)), use
        kp, ap = self.peak(self.kappa_peak[sign], corner, fz), self.peak(self.alpha_peak, corner, fz)
        sy = math.tan(alpha) / math.tan(ap)

        def forces(kappa):
            sx = kappa / kp
            s = math.hypot(sx, sy)
            if s < 1e-12:
                return 0.0, 0.0
            fx = abs(_mf52_fx_pure(tire, fz, sign * s * kp, 0.0)) * abs(sx) / s
            return fx, lateral_n(tire, fz, math.atan(s * math.tan(ap)), side, gamma) * sy / s

        best = minimize_scalar(lambda k: -forces(k)[0], bounds=(0.0, 0.5), method="bounded", options={"xatol": 1e-6})
        capacity = -best.fun
        if abs(fx_w) >= capacity:
            return forces(best.x)[1], abs(fx_w) / capacity
        kappa = brentq(lambda k: forces(k)[0] - abs(fx_w), 0.0, best.x, xtol=1e-9)
        return forces(kappa)[1], abs(fx_w) / capacity

    def with_grip(self, front=1.0, rear=1.0):
        probe = copy.copy(self)
        f = {**self.tires[0], "LMUY": self.tires[0]["LMUY"] * front}
        r = {**self.tires[2], "LMUY": self.tires[2]["LMUY"] * rear}
        probe.tires = (f, f, r, r)
        return probe


def with_front_v19(vehicle, tie_o_dy_m=0.0, tie_o_dx_m=0.0, rack_dy_m=0.0, rack_dz_m=0.0):
    v19 = copy.deepcopy(vehicle)
    v19["front"]["suspension"].update(copy.deepcopy(FRONT_V19_M))
    v19["front"]["suspension"]["tie_o_m"][0] += tie_o_dx_m
    v19["front"]["suspension"]["tie_o_m"][1] += tie_o_dy_m
    v19["front"]["steering"]["rack_pickup_m"] = [
        RACK_PICKUP_V19_M[0], RACK_PICKUP_V19_M[1] + rack_dy_m, RACK_PICKUP_V19_M[2] + rack_dz_m,
    ]
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


class Longitudinal:
    def __init__(self, decel_g, bias=None, diff=None, wheel_radius=None, rear_diff=None):
        self.decel_g, self.bias, self.diff, self.wheel_radius = decel_g, bias, diff, wheel_radius
        self.rear_diff = rear_diff

    def wheel_fx(self, total_n):
        if self.diff is not None:
            return (0.0, 0.0) + diff_split(total_n, self.diff, self.wheel_radius)
        front, rear = -self.bias * total_n / 2, -(1.0 - self.bias) * total_n
        if self.rear_diff is None:
            return (front, front, rear / 2, rear / 2)
        return (front, front) + diff_split(rear, self.rear_diff, self.wheel_radius)


def diff_split(total_n, diff, radius):
    preload, lock, kinetic = diff
    torque = total_n * radius
    transfer = kinetic * (2.0 * preload + lock * abs(torque))
    return ((torque + transfer) / 2 / radius, (torque - transfer) / 2 / radius)


class ToeCurve:
    def __init__(self, curve, toe_out_deg):
        self.curve, self.half = curve, math.radians(toe_out_deg) / 2

    def __call__(self, outer):
        return self.curve(outer + self.half) + self.half


class ConstantAckermann:
    def __init__(self, pct, track, wheelbase):
        self.k = pct / 100.0 * track / wheelbase

    def __call__(self, outer):
        return math.atan2(math.tan(outer), 1.0 - self.k * math.tan(outer))


class TableCurve:
    def __init__(self, table):
        self.outer, self.inner = table[:, 2].copy(), table[:, 1].copy()

    def __call__(self, outer):
        return float(np.interp(outer, self.outer, self.inner))


def lateral_n(tire, fz, alpha, side, gamma=0.0):
    return -side * _mf52_fy_pure(tire, fz, side * alpha, side * gamma)


def peak_lateral_n(tire, fz, side):
    res = minimize_scalar(
        lambda a: -lateral_n(tire, fz, a, side), bounds=(0.0, 0.6), method="bounded", options={"xatol": 1e-7},
    )
    return -res.fun


def state(car, curve, radius, side, beta, outer, ay_g, lon=None, lon_n=0.0):
    speed = math.sqrt(ay_g * G * radius)
    yaw_rate = speed / radius
    steer = (curve(outer), outer, 0.0, 0.0)
    decel_g = lon.decel_g if lon else 0.0
    wheel_fx = lon.wheel_fx(lon_n) if lon else (0.0, 0.0, 0.0, 0.0)
    ax_body = -decel_g * math.cos(beta) - ay_g * math.sin(beta)
    ay_body = -decel_g * math.sin(beta) + ay_g * math.cos(beta)
    loads = car.loads(ay_body, -ax_body)
    fx = fy = mz = 0.0
    alphas, forces, uses = [], [], []
    for i, ((x, y), delta, fz, fx_w) in enumerate(zip(car.corners, steer, loads, wheel_fx)):
        alpha = delta - math.atan2(speed * math.sin(beta) + yaw_rate * x, speed * math.cos(beta) - yaw_rate * y)
        force, use = car.lateral(i, fz, alpha, fx_w, side)
        body_x = fx_w * math.cos(delta) - force * math.sin(delta)
        body_y = fx_w * math.sin(delta) + force * math.cos(delta)
        fx += body_x
        fy += body_y
        mz += x * body_y - y * body_x
        alphas.append(alpha)
        forces.append(force)
        uses.append(use)
    drive = -car.mass * ay_g * G * math.sin(beta) - fx
    return {
        "speed": speed, "steer": steer, "loads": loads, "alphas": alphas, "forces": forces,
        "brake_use": uses, "wheel_fx": wheel_fx, "fx": fx, "fy": fy, "mz": mz, "drive_n": drive,
    }


def balance(car, curve, radius, side, z, ay_g, lon=None):
    s = state(car, curve, radius, side, z[0], z[1], ay_g, lon, z[2] if lon else 0.0)
    weight = car.mass * G
    decel_g = lon.decel_g if lon else 0.0
    lateral = (s["fy"] - weight * (ay_g * math.cos(z[0]) - decel_g * math.sin(z[0]))) / weight
    yaw = s["mz"] / (weight * car.wheelbase)
    if not lon:
        return [lateral, yaw]
    return [(s["fx"] + weight * (decel_g * math.cos(z[0]) + ay_g * math.sin(z[0]))) / weight, lateral, yaw]


def is_feasible(car, curve, radius, side, z, ay_g, lon=None, eps=1e-4):
    s = state(car, curve, radius, side, z[0], z[1], ay_g, lon, z[2] if lon else 0.0)
    if max(s["brake_use"]) >= 1.0:
        return False
    rise = [
        car.lateral(i, fz, a + eps, fx_w, side)[0] - f
        for i, (fz, a, fx_w, f) in enumerate(zip(s["loads"], s["alphas"], s["wheel_fx"], s["forces"]))
    ]
    return min(rise[0] + rise[1], rise[2] + rise[3]) >= -1e-6 * eps * car.mass * G


def trim(car, curve, radius, side, ay_g, outer_max, guess=None, lon=None):
    rear_path = car.wheelbase * car.front_frac / radius
    outer_path = math.atan(car.wheelbase / (radius + car.track_front / 2))
    lower, upper, extra = [-0.4, 0.0], [0.4, outer_max], ()
    if lon:
        lower, upper, extra = lower + [0.0], upper + [3.0 * car.mass * G], (car.mass * G * max(abs(lon.decel_g), 0.05),)
    guesses = [guess] if guess is not None else [
        (rear_path - db, min(max(outer_path + ds, 0.0), outer_max)) + extra
        for db in (0.0, 0.03, 0.06) for ds in (0.0, 0.05, -0.05, 0.10)
    ]
    for start in guesses:
        res = least_squares(
            lambda z: balance(car, curve, radius, side, z, ay_g, lon),
            start, bounds=(lower, upper), xtol=1e-12, ftol=1e-12, gtol=1e-12,
        )
        if max(abs(v) for v in res.fun) < 1e-7 and is_feasible(car, curve, radius, side, res.x, ay_g, lon):
            return res.x
    return None


def limit_ay(car, curve, radius, side, outer_max, lon=None, low=0.1, high=2.5, tol=2e-5):
    best = trim(car, curve, radius, side, low, outer_max, lon=lon)
    if best is None:
        return None
    while high - low > tol:
        mid = 0.5 * (low + high)
        z = trim(car, curve, radius, side, mid, outer_max, best, lon)
        if z is None:
            z = trim(car, curve, radius, side, mid, outer_max, lon=lon)
        if z is None:
            high = mid
        else:
            low, best = mid, z
    return best, low


def probe_gain(car, curve, radius, side, outer_max, ay_g, lon=None):
    limit = limit_ay(car, curve, radius, side, outer_max, lon)
    return float("nan") if limit is None else limit[1] / ay_g - 1.0


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


def build_cases(ggv, tir, curves, outer_max, biases, orion, diffs, rear_radius, steer_targets):
    cases = []
    for lky_name, lky in LKY_CASES.items():
        tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": lky}
        for lltd_offset in LLTD_OFFSETS:
            car = Car(ggv, (tire,) * 4, ggv.lltd + lltd_offset)
            for side_name, side in SLIP_SIDES.items():
                for radius in RADII_M:
                    cases.append({
                        "kind": "main", "car": car, "curves": curves, "outer_max": outer_max,
                        "radius": radius, "side": side, "max_drive_force": ggv.max_drive_force,
                        "labels": {
                            "tire_case": lky_name, "slip_side": side_name, "lltd_front": round(car.lltd, 4),
                            "lltd_offset": lltd_offset, "radius_m": radius,
                        },
                    })
    braking_curves = {k: v for k, v in curves.items() if k not in ("-50%", "-25%")}
    for model, lky_name, lltd_offset in BRAKE_CASES:
        tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[lky_name]}
        car = Car(ggv, (tire,) * 4, ggv.lltd + lltd_offset)
        car.combined = model
        for bias in biases:
            for brake_g in BRAKE_G:
                if brake_g == 0.0 and model != "ellipse":
                    continue
                for radius in BRAKE_RADII_M:
                    cases.append({
                        "kind": "braking", "car": car, "curves": braking_curves, "outer_max": outer_max,
                        "radius": radius, "lon": Longitudinal(brake_g, bias=bias) if brake_g > 0.0 else None,
                        "probe": model == "ellipse" and (lky_name, lltd_offset) == (NOMINAL[0], NOMINAL[2]),
                        "labels": {
                            "combined_slip": model, "tire_case": lky_name, "lltd_front": round(car.lltd, 4),
                            "lltd_offset": lltd_offset, "brake_bias_front": bias, "brake_g": brake_g,
                            "radius_m": radius,
                        },
                    })
    drive_curves = braking_curves
    tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[NOMINAL[0]]}
    for model in DRIVE_MODELS:
        car = Car(ggv, (tire,) * 4, ggv.lltd + NOMINAL[2])
        car.combined = model
        for diff_name, (preload, drive_lock, _, kinetic) in diffs.items():
            diff = (preload, drive_lock, kinetic)
            for drive_g in DRIVE_G:
                for radius in DRIVE_RADII_M:
                    cases.append({
                        "kind": "drive", "car": car, "curves": drive_curves, "outer_max": outer_max,
                        "radius": radius, "lon": Longitudinal(-drive_g, diff=diff, wheel_radius=rear_radius),
                        "probe": model == "ellipse" and diff_name != "open",
                        "labels": {"combined_slip": model, "diff": diff_name, "drive_g": drive_g, "radius_m": radius},
                    })
    car = Car(ggv, (tire,) * 4, ggv.lltd + NOMINAL[2])
    toe_curves = {"Front v19": curves["Front v19"]}
    toe_curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in TOE_ACKERMANN_PCT})
    for toe in TOE_OUT_DEG:
        for radius in TOE_RADII_M:
            cases.append({
                "kind": "toe", "car": car, "curves": {k: ToeCurve(v, toe) for k, v in toe_curves.items()},
                "outer_max": outer_max, "radius": radius, "lon": None, "probe": False,
                "labels": {"toe_out_deg": toe, "radius_m": radius},
            })
    car = Car(ggv, (tire,) * 4, ggv.lltd + NOMINAL[2])
    for diff_name, (preload, _, coast_lock, kinetic) in diffs.items():
        if diff_name == "open":
            continue
        for bias in REGEN_BIAS:
            for brake_g in BRAKE_G[1:]:
                for radius in BRAKE_RADII_M:
                    lon = Longitudinal(brake_g, bias=bias, wheel_radius=rear_radius, rear_diff=(preload, coast_lock, kinetic))
                    cases.append({
                        "kind": "regen", "car": car, "curves": braking_curves, "outer_max": outer_max, "radius": radius,
                        "lon": lon, "probe": True,
                        "labels": {"diff": diff_name, "front_share": bias, "brake_g": brake_g, "radius_m": radius},
                    })
    for name, field, delta in MASS_CASES:
        variant = types.SimpleNamespace(**{f: getattr(ggv, f) for f in GGV_FIELDS})
        if field == "camber":
            car = Car(variant, (tire,) * 4, ggv.lltd + NOMINAL[2], camber_deg=TEAM_CAMBER_DEG)
        else:
            setattr(variant, field, getattr(variant, field) + delta)
            car = Car(variant, (tire,) * 4, ggv.lltd + NOMINAL[2])
        for radius in TOE_RADII_M:
            cases.append({
                "kind": "mass", "car": car, "curves": braking_curves, "outer_max": outer_max, "radius": radius,
                "lon": None, "probe": False, "labels": {"variant": name, "radius_m": radius},
            })
    cases.append({"kind": "track"})
    for target, target_deg in steer_targets.items():
        cases.append({
            "kind": "fix", "vehicle": orion, "target_deg": target_deg, "target_pct": target,
            "track": ggv.track_front, "wheelbase": ggv.wheelbase,
        })
    for target in TIE_O_TARGETS_PCT:
        cases.append({"kind": "tie_o", "vehicle": orion, "target": target, "track": ggv.track_front, "wheelbase": ggv.wheelbase})
    return cases


def cost(case):
    if case["kind"] in ("braking", "drive") and case["car"].combined != "ellipse":
        return 0
    return {"track": 0, "fix": 0, "main": 1, "tie_o": 2, "braking": 3, "drive": 3, "regen": 3, "toe": 4, "mass": 4}[case["kind"]]


def solve_case(case):
    if case["kind"] == "main":
        return solve_main_case(case)
    if case["kind"] in ("braking", "drive", "toe", "regen", "mass"):
        return solve_braking_case(case)
    if case["kind"] == "track":
        return minimum_curvature_line()
    if case["kind"] == "fix":
        return steering_fix(case["vehicle"], case["target_deg"], case["target_pct"], case["track"], case["wheelbase"])
    return tie_o_shift(case["vehicle"], case["target"], case["track"], case["wheelbase"])


def solve_main_case(case):
    car, curves, outer_max, radius, side = case["car"], case["curves"], case["outer_max"], case["radius"], case["side"]
    exit_g = exit_accel_g(car, case["max_drive_force"])
    rows, ref_ay = [], None
    for name, curve in curves.items():
        labels = {**case["labels"], "curve": name}
        limit = limit_ay(car, curve, radius, side, outer_max)
        if limit is None:
            rows.append({**labels, "ay_max_g": float("nan"), "ay_change_pct": float("nan")})
            continue
        (beta, outer), ay = limit
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
            **labels, "ay_max_g": ay, "ay_change_pct": 100.0 * (ay / ref_ay - 1.0),
            "speed_mps": s["speed"], "beta_deg": math.degrees(beta),
            "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
            "ackermann_pct_at_limit": float(ackermann_pct(inner, outer, car.track_front, car.wheelbase)),
            **{f"alpha_{c}_deg": math.degrees(a) for c, a in zip(("fl", "fr", "rl", "rr"), s["alphas"])},
            **{f"fz_{c}_n": f for c, f in zip(("fl", "fr", "rl", "rr"), s["loads"])},
            "front_grip_used": sum(s["forces"][:2]) / sum(peaks[:2]),
            "rear_grip_used": sum(s["forces"][2:]) / sum(peaks[2:]),
            "ay_gain_pct_per_1pct_front_grip": 100.0 * front_gain,
            "ay_gain_pct_per_1pct_rear_grip": 100.0 * rear_gain,
            "limiting_axle": limiting_axle(front_gain, rear_gain),
            "drive_n_at_80pct_ref_ay": drag,
            "turn_180_delta_s": turn_time_delta_s(radius, ref_ay, ay, exit_g),
            "exit_accel_g": exit_g,
        })
    return rows


def solve_braking_case(case):
    car, curves, outer_max, radius, lon = case["car"], case["curves"], case["outer_max"], case["radius"], case["lon"]
    rows, ref_ay = [], None
    for name, curve in curves.items():
        labels = {**case["labels"], "curve": name}
        limit = limit_ay(car, curve, radius, 1.0, outer_max, lon)
        if limit is None:
            rows.append({**labels, "ay_max_g": float("nan"), "ay_change_pct": float("nan")})
            continue
        z, ay = limit
        ref_ay = ref_ay or ay
        s = state(car, curve, radius, 1.0, z[0], z[1], ay, lon, z[2] if lon else 0.0)
        row = {
            **labels, "ay_max_g": ay, "ay_change_pct": 100.0 * (ay / ref_ay - 1.0),
            "beta_deg": math.degrees(z[0]),
            "inner_deg": math.degrees(s["steer"][0]), "outer_deg": math.degrees(z[1]),
            **{f"alpha_{c}_deg": math.degrees(a) for c, a in zip(("fl", "fr", "rl", "rr"), s["alphas"])},
            **{f"fz_{c}_n": f for c, f in zip(("fl", "fr", "rl", "rr"), s["loads"])},
            **{f"brake_use_{c}": u for c, u in zip(("fl", "fr", "rl", "rr"), s["brake_use"])},
            "ackermann_pct_at_limit": float(ackermann_pct(s["steer"][0], z[1], car.track_front, car.wheelbase)),
            **{f"fx_{c}_n": f for c, f in zip(("fl", "fr", "rl", "rr"), s["wheel_fx"])},
            "longitudinal_n": z[2] if lon else 0.0,
            "max_wheel_use": max(s["brake_use"]),
        }
        if case["probe"]:
            front = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, 1.0, outer_max, ay, lon)
            rear = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, 1.0, outer_max, ay, lon)
            row["ay_gain_pct_per_1pct_front_grip"] = 100.0 * front
            row["ay_gain_pct_per_1pct_rear_grip"] = 100.0 * rear
            row["limiting_axle"] = limiting_axle(front, rear)
        rows.append(row)
    return rows


def minimum_curvature_line():
    config = load_yaml(repo_root() / LAP_CONFIG)["track"]
    corridor = TrackCorridor.from_csv(repo_root() / config["boundary_csv"])
    ay = np.linspace(0.0, 15.0, 16)
    ax = np.sqrt(np.maximum(15.0**2 - ay**2, 0.0))
    ggv = GGVMap(tuple(_GGVSlice(speed, ay, ax, -ax) for speed in (5.0, 40.0)))
    result = optimize_racing_line(
        corridor, ggv, mode="minimum_curvature", vehicle_width_m=config["vehicle_width_m"],
        safety_margin_m=config["safety_margin_m"], sample_step_m=config["sample_step_m"],
    )
    return {
        "track": config["boundary_csv"], "curvature": result.line.curvature_per_m,
        "segment": result.line.segment_length_m, "length_m": float(result.line.track_length_m),
    }


def track_corners(line):
    tight = np.abs(line["curvature"]) > 1.0 / CORNER_RADIUS_MAX_M
    shift = int(np.argmin(tight))
    kappa, seg, tight = (np.roll(a, -shift) for a in (np.abs(line["curvature"]), line["segment"], tight))
    corners, i = [], 0
    while i < len(tight):
        if not tight[i]:
            i += 1
            continue
        j = i
        while j < len(tight) and tight[j]:
            j += 1
        corners.append((float(np.sum(kappa[i:j] * seg[i:j])), float(1.0 / kappa[i:j].max())))
        i = j
    return corners


def balanced_lltd(ggv, tir, curve, outer_max):
    tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[NOMINAL[0]]}
    car = Car(ggv, (tire,) * 4, 0.5)

    def negative_ay(lltd):
        car.lltd = lltd
        limit = limit_ay(car, curve, BALANCE_RADIUS_M, 1.0, outer_max)
        return 0.0 if limit is None else -limit[1]

    res = minimize_scalar(negative_ay, bounds=(0.2, 0.8), method="bounded", options={"xatol": 1e-3})
    car.lltd = res.x
    ay = -res.fun
    return res.x, {
        "radius_m": BALANCE_RADIUS_M, "lltd_front": round(res.x, 4), "ay_max_g": round(ay, 4),
        "ay_gain_pct_per_1pct_front_grip": round(100.0 * probe_gain(car.with_grip(front=GRIP_PROBE), curve, BALANCE_RADIUS_M, 1.0, outer_max, ay), 3),
        "ay_gain_pct_per_1pct_rear_grip": round(100.0 * probe_gain(car.with_grip(rear=GRIP_PROBE), curve, BALANCE_RADIUS_M, 1.0, outer_max, ay), 3),
    }


def needed_mean_steer_deg(car, curve, radius, outer_max):
    (beta, outer), ay = limit_ay(car, curve, radius, 1.0, outer_max)
    return math.degrees(0.5 * (curve(outer) + outer))


def lock_check(table, rows, wheelbase):
    nominal = sorted((r for r in rows if is_nominal(r) and r["curve"] == "Front v19"), key=lambda r: r["radius_m"])
    radii = [r["radius_m"] for r in nominal]
    need = [0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal]
    mean = np.degrees(0.5 * (table[:, 1] + table[:, 2]))
    reach = float(np.interp(RACK_TRAVEL_MM, table[:, 0], mean))
    outer = math.radians(float(np.interp(RACK_TRAVEL_MM, table[:, 0], np.degrees(table[:, 2]))))
    return {
        "rack_travel_mm": RACK_TRAVEL_MM,
        "mean_steer_at_travel_deg": round(reach, 1),
        "mean_steer_needed_at_limit_deg": {f"{r:g}m": round(n, 1) for r, n in zip(radii, need)},
        "tightest_cg_radius_at_limit_m": round(float(np.interp(-reach, [-n for n in need], radii)), 2),
        "rear_axle_radius_walking_m": round(wheelbase / math.tan(math.radians(reach)), 2),
        "outer_front_wheel_center_radius_walking_m": round(wheelbase / math.sin(outer), 2),
        "rack_needed_for_3p5m_at_limit_mm": round(float(np.interp(need[0], mean, table[:, 0])), 1),
    }


def arm_offset_mm(vehicle):
    s = vehicle["front"]["suspension"]
    upper, lower, tie = (np.array(s[k]) for k in ("upper_o_m", "lower_o_m", "tie_o_m"))
    axis = (upper - lower) / np.linalg.norm(upper - lower)
    rel = tie - lower
    return round(1000.0 * float(np.linalg.norm(rel - np.dot(rel, axis) * axis)), 1)


def lock_geometry(vehicle, rack_mm):
    corner = CornerKinematics.from_vehicle(vehicle, "front")
    guess = np.zeros(3)
    for rack in np.append(np.arange(0.0, rack_mm, 0.5), rack_mm):
        x, points, _ = corner.solve_jounce(0.0, guess, rack_displacement_m=rack / 1000.0)
        guess = x
    axis = (points.upper_o - points.lower_o) / np.linalg.norm(points.upper_o - points.lower_o)
    rel = points.tie_o - points.lower_o
    arm = rel - np.dot(rel, axis) * axis
    rod = corner.rack_pickup_initial + np.array([0.0, rack_mm / 1000.0, 0.0]) - points.tie_o
    rod = rod - np.dot(rod, axis) * axis
    angle = math.degrees(math.acos(np.clip(np.dot(arm, rod) / (np.linalg.norm(arm) * np.linalg.norm(rod)), -1.0, 1.0)))
    toe = {}
    for jounce in (-BUMP_MM, 0.0, BUMP_MM):
        xj, pj, rj = corner.solve_jounce(jounce / 1000.0, guess, rack_displacement_m=rack_mm / 1000.0)
        toe[jounce] = corner.curve_values(pj, xj, rj)["toe_deg"]
    return {
        "arm_to_tie_rod_angle_deg": round(angle, 1),
        "toggle_margin_deg": round(min(angle, 180.0 - angle), 1),
        "bump_toe_change_deg": {f"{j:+g}mm": round(toe[j] - toe[0.0], 3) for j in (-BUMP_MM, BUMP_MM)},
    }


def steering_fix(vehicle, target_deg, target_pct, track, wheelbase):
    fix_rack = STEER_RESERVE * RACK_TRAVEL_MM
    rack = np.append(np.arange(0.0, fix_rack, 0.5), fix_rack)

    def build(d):
        return with_front_v19(vehicle, d[1] / 1000.0, d[0] / 1000.0, d[2] / 1000.0, d[3] / 1000.0)

    def errors(d):
        table, corner = steer_table(build(d), rack)
        if len(table) == 0 or table[-1, 0] < fix_rack:
            return None
        mean = math.degrees(0.5 * (table[-1, 1] + table[-1, 2]))
        pct = float(ackermann_pct(table[-1, 1], table[-1, 2], track, wheelbase))
        return [mean - target_deg, pct - target_pct, bump_toe_deg(corner, BUMP_MM), bump_toe_deg(corner, -BUMP_MM)]

    def residual(d):
        e = errors(d)
        return [10.0] * 4 if e is None else [e[0], e[1] / 10.0, 20.0 * e[2], 20.0 * e[3]]

    res = least_squares(
        residual, [-20.0, target_pct / 4.0, 0.0, 0.0],
        bounds=([-60.0, -30.0, -60.0, -60.0], [10.0, 50.0, 60.0, 60.0]), diff_step=1e-2, xtol=1e-8,
    )
    e = errors(res.x)
    solved = e is not None and abs(e[0]) < 0.1 and abs(e[1]) < 1.0 and max(abs(e[2]), abs(e[3])) < 0.02
    fixed = build(res.x)
    base = with_front_v19(vehicle)
    table, corner = steer_table(fixed)
    suspension = fixed["front"]["suspension"]
    return {
        "target_ackermann_pct": target_pct,
        "target_mean_steer_deg": round(target_deg, 2),
        "target_rack_mm": round(fix_rack, 2),
        "solved": solved,
        "tie_o_shift_mm": {"x_forward": round(res.x[0], 1), "y_outboard": round(res.x[1], 1)},
        "rack_pickup_shift_mm": {"y_outboard": round(res.x[2], 1), "z_up": round(res.x[3], 1)},
        "tie_o_new_mm": [round(1000.0 * v, 1) for v in suspension["tie_o_m"]],
        "tie_o_inboard_of_wheel_center_plane_mm": round(1000.0 * (suspension["wheel_center_m"][1] - suspension["tie_o_m"][1]), 1),
        "arm_offset_from_kingpin_mm": {"front_v19": arm_offset_mm(base), "fix": arm_offset_mm(fixed)},
        "mean_steer_deg": {
            f"{r:g}mm": round(math.degrees(0.5 * (at_rack(table, r, 1) + at_rack(table, r, 2))), 1)
            for r in (fix_rack, RACK_TRAVEL_MM)
        },
        "inner_outer_at_travel_deg": [round(math.degrees(at_rack(table, RACK_TRAVEL_MM, c)), 1) for c in (1, 2)],
        "ackermann_pct": {
            f"{r:g}mm": round(float(ackermann_pct(at_rack(table, r, 1), at_rack(table, r, 2), track, wheelbase)), 1)
            for r in (10.0, 20.0, fix_rack, RACK_TRAVEL_MM)
        },
        "bump_toe_deg_at_zero_rack": {f"{j:+g}mm": round(bump_toe_deg(corner, j), 3) for j in (-BUMP_MM, BUMP_MM)},
        "inner_wheel_at_travel": lock_geometry(fixed, RACK_TRAVEL_MM),
        "front_v19_inner_wheel_at_travel": lock_geometry(base, RACK_TRAVEL_MM),
    }


def braking_limit_g(car, bias):
    fz = car.mass * G * car.front_frac / 2
    mu = car.peak(car.fx_max_n[-1], 0, fz) / fz
    return mu * car.front_frac / (bias - mu * car.cg_height / car.wheelbase)


def lap_gain_s(corners, rows, exit_g, entry_g):
    nominal = [r for r in rows if is_nominal(r)]

    def ay_at(curve, radius):
        values = [next(r["ay_max_g"] for r in nominal if r["radius_m"] == rr and r["curve"] == curve) for rr in RADII_M]
        return float(np.interp(radius, RADII_M, values))

    gains = {}
    for curve in ("+50%", "+75%", "+100%"):
        bounds = []
        for exit_a, entry_a in zip(exit_g, entry_g):
            total = 0.0
            for theta, radius in corners:
                r = max(radius, RADII_M[0])
                v0, v1 = math.sqrt(ay_at("Front v19", r) * G * r), math.sqrt(ay_at(curve, r) * G * r)
                total += theta * r * (1.0 / v1 - 1.0 / v0) - (v1 - v0) / (exit_a * G) - (v1 - v0) / (entry_a * G)
            bounds.append(round(total, 3))
        gains[curve] = bounds
    return gains


def optimum_check(car, rows):
    out = {}
    for radius in RADII_M[:4]:
        row = next(r for r in rows if is_nominal(r) and r["radius_m"] == radius and r["curve"] == "+75%")
        beta, speed = math.radians(row["beta_deg"]), row["speed_mps"]
        yaw = speed / radius
        path = [math.atan2(speed * math.sin(beta) + yaw * x, speed * math.cos(beta) - yaw * y) for x, y in car.corners[:2]]
        loads = (row["fz_fl_n"], row["fz_fr_n"])
        peaks = [car.peak(car.alpha_peak, i, fz) for i, fz in enumerate(loads)]
        weighted = []
        for i, (theta, fz) in enumerate(zip(path, loads)):
            lever = car.corners[i][1]
            res = minimize_scalar(
                lambda a: -(car.wheelbase * math.cos(theta + a) + lever * math.sin(theta + a)) * lateral_n(car.tires[i], fz, a, 1.0, car.camber[i]),
                bounds=(0.0, 0.3), method="bounded", options={"xatol": 1e-7},
            )
            weighted.append(res.x)

        def optimum(alphas):
            inner, outer = path[0] + alphas[0], path[1] + alphas[1]
            return float(ackermann_pct(inner, outer, car.track_front, car.wheelbase)), inner, outer

        simple = optimum(peaks)[0]
        vehicle, inner, outer = optimum(weighted)
        per_toe = float(ackermann_pct(inner + math.radians(1.0), outer, car.track_front, car.wheelbase)) - vehicle
        out[f"{radius:g}m"] = {
            "path_angle_difference_deg": round(math.degrees(path[0] - path[1]), 2),
            "rear_slip_deg": round(0.5 * (row["alpha_rl_deg"] + row["alpha_rr_deg"]), 2),
            "peak_slip_inner_outer_deg": [round(math.degrees(a), 2) for a in peaks],
            "yaw_weighted_slip_inner_outer_deg": [round(math.degrees(a), 2) for a in weighted],
            "peak_slip_optimum_pct": round(simple, 1),
            "yaw_weighted_optimum_pct": round(vehicle, 1),
            "points_per_deg_toe_out": round(per_toe, 1),
        }
    return out


def limiting_axle(front_gain, rear_gain):
    return "front" if np.nan_to_num(front_gain, nan=-1.0) > np.nan_to_num(rear_gain, nan=-1.0) else "rear"


def braking_summary(rows):
    def label(r):
        return f"{r['combined_slip']}, bias {r['brake_bias_front']:.2f}, {r['brake_g']:g} g, {r['radius_m']:g} m"

    def key(r):
        return (r["combined_slip"], r["tire_case"], r["lltd_offset"], r["brake_bias_front"], r["brake_g"], r["radius_m"])

    nominal, best = {}, {}
    for r in rows:
        if r["tire_case"] == NOMINAL[0] and r["lltd_offset"] == NOMINAL[2]:
            nominal.setdefault(label(r), {})[r["curve"]] = {
                "ay_max_g": round(r["ay_max_g"], 3), "ay_change_pct": round(r["ay_change_pct"], 2),
                "inner_front_brake_use": None if "brake_use_fl" not in r else round(r["brake_use_fl"], 2),
                "limiting_axle": r.get("limiting_axle"),
            }
    for k in sorted({key(r) for r in rows}):
        subset = [r for r in rows if key(r) == k and r["curve"] != "Front v19"]
        winner = max(subset, key=lambda r: np.nan_to_num(r["ay_max_g"], nan=-1.0))
        best.setdefault(label(winner), []).append(winner["curve"])
    return {"nominal": nominal, "best_constant_curve_per_case": best}


def drive_summary(rows):
    out = {}
    for r in rows:
        key = f"{r['combined_slip']}, {r['diff']}, {r['drive_g']:g} g, {r['radius_m']:g} m"
        out.setdefault(key, {})[r["curve"]] = {
            "ay_max_g": round(r["ay_max_g"], 3), "ay_change_pct": round(r["ay_change_pct"], 2),
            "limiting_axle": r.get("limiting_axle"),
            "wheelspin_limited": bool(r.get("max_wheel_use", 0.0) > 0.97),
        }
    return out


def variant_summary(rows, keys):
    out = {}
    for r in rows:
        entry = out.setdefault(", ".join(f"{k} {r[k]}" for k in keys), {"change_vs_v19_pct": {}})
        entry["change_vs_v19_pct"][r["curve"]] = round(r["ay_change_pct"], 2)
        if r["curve"] == "Front v19":
            entry["v19_ay_max_g"] = round(r["ay_max_g"], 3)
        if r.get("limiting_axle"):
            entry.setdefault("limiting_axle", {})[r["curve"]] = r["limiting_axle"]
    for entry in out.values():
        changes = {k: v for k, v in entry["change_vs_v19_pct"].items() if k != "Front v19" and v == v}
        entry["best_curve"] = max(changes, key=changes.get)
    return out


def toe_summary(rows):
    ref = {r["radius_m"]: r["ay_max_g"] for r in rows if r["toe_out_deg"] == 0.0 and r["curve"] == "Front v19"}
    out = {}
    for r in rows:
        key = f"toe-out {r['toe_out_deg']:g} deg, {r['radius_m']:g} m"
        entry = out.setdefault(key, {"change_vs_v19_zero_toe_pct": {}})
        entry["change_vs_v19_zero_toe_pct"][r["curve"]] = round(100.0 * (r["ay_max_g"] / ref[r["radius_m"]] - 1.0), 2)
    for entry in out.values():
        changes = {k: v for k, v in entry["change_vs_v19_zero_toe_pct"].items() if k != "Front v19"}
        entry["best_curve"] = max(changes, key=changes.get)
        entry["best_change_pct"] = changes[entry["best_curve"]]
    return out


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


def plot_grip(path, rows, v19_pct, braking, bias):
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.0, 4.2), facecolor=SURFACE)
    pct = np.array(ACKERMANN_PCT, dtype=float)
    plot_braking(right, braking, bias)
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
    left.axhline(0.0, color=MUTED, lw=0.8)
    left.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    left.set_ylabel("Max lateral g change vs Front v19 (%)", color=INK)
    left.set_title("Apex grip by corner radius (diamond = Front v19)", color=INK, loc="left", fontsize=11)
    for ax in (left, right):
        style(ax)
    left.legend(frameon=False, fontsize=8, labelcolor=INK)
    right.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_braking(ax, braking, bias):
    names = [n for n in (f"{p:+d}%" for p in ACKERMANN_PCT) if any(r["curve"] == n for r in braking)]
    pct = np.array([float(n.rstrip("%")) for n in names])
    for brake_g, color in zip(BRAKE_G, BRAKE_COLORS):
        for model, style_ in (("slip_norm", "-"), ("ellipse", "--")):
            picked = {
                r["curve"]: r["ay_change_pct"] for r in braking
                if r["combined_slip"] == model and r["tire_case"] == NOMINAL[0] and r["lltd_offset"] == NOMINAL[2]
                and r["brake_bias_front"] == bias and r["brake_g"] == brake_g and r["radius_m"] == BRAKE_RADII_M[0]
            }
            if len(picked) < len(names) or (brake_g == 0.0 and model != "ellipse"):
                continue
            label = f"{brake_g:g} g" if brake_g == 0.0 or model == "slip_norm" else None
            ax.plot(pct, [picked[n] for n in names], color=color, lw=2, ls="-" if brake_g == 0.0 else style_, marker="o", ms=4, label=label)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    ax.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    ax.set_ylabel("Max lateral g change vs Front v19 (%)", color=INK)
    ax.set_title(f"Trail braking at R = {BRAKE_RADII_M[0]:g} m, {100 * bias:.0f}% front bias", color=INK, loc="left", fontsize=11)
    ax.text(0.98, 0.62, "solid: normalized slip\ndashed: friction ellipse", transform=ax.transAxes, fontsize=8, color=MUTED, ha="right")


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
    ggv = types.SimpleNamespace(**{f: getattr(projection.ggv, f) for f in GGV_FIELDS})
    for field, value in TEAM_2027.items():
        setattr(ggv, field, value)
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

    curves = {"Front v19": TableCurve(v19_table)}
    curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in ACKERMANN_PCT})
    outer_max = float(v19_table[-1, 2])
    tir = parse_tir(tire_templates_root(v19) / f"{v19['front']['tire']['template']}.tir")
    ggv.lltd, balance = balanced_lltd(ggv, tir, curves["Front v19"], outer_max)
    nominal_tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[NOMINAL[0]]}
    nominal_car = Car(ggv, (nominal_tire,) * 4, ggv.lltd)
    steer_targets = {
        pct: needed_mean_steer_deg(nominal_car, ConstantAckermann(pct, ggv.track_front, ggv.wheelbase), RADII_M[0], outer_max)
        for pct in STEER_FIX_TARGETS_PCT
    }
    dl = v19["powertrain"]["pDriveline"]
    diffs = {
        "open": (0.0, 0.0, 0.0, 0.0),
        "orion": (dl["diff_T_preload"], dl["diff_lockFractionAccel"], dl["diff_lockFractionDecel"], dl["diff_kineticFrictionRatio"]),
        "planned": PLANNED_DIFF + (dl["diff_kineticFrictionRatio"],),
    }
    cases = build_cases(
        ggv, tir, curves, outer_max, (float(v19["brake"]["front_bias"]), BRAKE_BIAS_EXTRA), orion,
        diffs, float(v19["rear"]["wheel"]["radius_m"]), steer_targets,
    )
    order = sorted(range(len(cases)), key=lambda i: (cost(cases[i]), i))
    solved = map_cases(solve_case, [cases[i] for i in order])
    results = [None] * len(cases)
    for i, result in zip(order, solved):
        results[i] = result
    rows = [row for case, result in zip(cases, results) if case["kind"] == "main" for row in result]
    braking = [row for case, result in zip(cases, results) if case["kind"] == "braking" for row in result]
    tie_o = [result for case, result in zip(cases, results) if case["kind"] == "tie_o"]
    drive = [row for case, result in zip(cases, results) if case["kind"] == "drive" for row in result]
    toe = [row for case, result in zip(cases, results) if case["kind"] == "toe" for row in result]
    regen = [row for case, result in zip(cases, results) if case["kind"] == "regen" for row in result]
    line = next(result for case, result in zip(cases, results) if case["kind"] == "track")
    fix = [result for case, result in zip(cases, results) if case["kind"] == "fix"]
    corners = track_corners(line)
    exit_g = next(r["exit_accel_g"] for r in rows if is_nominal(r))
    entry_g = braking_limit_g(nominal_car, float(v19["brake"]["front_bias"]))
    mass = [row for case, result in zip(cases, results) if case["kind"] == "mass" for row in result]
    for name, table in (("drive.csv", drive), ("toe.csv", toe), ("regen.csv", regen), ("mass_cg.csv", mass)):
        with (out / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(max(table, key=len)))
            writer.writeheader()
            writer.writerows(table)

    with (out / "limits.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(max(rows, key=len)))
        writer.writeheader()
        writer.writerows(rows)

    with (out / "braking.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(max(braking, key=len)))
        writer.writeheader()
        writer.writerows(braking)

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
    plot_grip(out / "grip_vs_ackermann.png", rows, v19_pct, braking, float(v19["brake"]["front_bias"]))

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
            "note": "Front v19 steering hardpoints, team 2027 mass and CG, balanced LLTD, Orion rear and tire",
            "mass_kg": round(ggv.mass, 2), "cg_height_m": round(ggv.cg_height, 4),
            "front_static_frac": round(ggv.front_static_frac, 4), "wheelbase_m": round(ggv.wheelbase, 4),
            "track_front_m": round(ggv.track_front, 4), "track_rear_m": round(ggv.track_rear, 4),
            "lltd_front": round(ggv.lltd, 4), "lltd_source": f"balanced at R = {BALANCE_RADIUS_M:g} m",
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
        "tie_o_shift_for_target": tie_o,
        "braking": braking_summary(braking),
        "drive": drive_summary(drive),
        "toe": toe_summary(toe),
        "regen_through_diff": variant_summary(regen, ("diff", "front_share", "brake_g", "radius_m")),
        "team_2027_inputs": {
            "mass_kg": round(TEAM_2027["mass"], 1), "cg_height_m": round(TEAM_2027["cg_height"], 4),
            "front_static_frac": TEAM_2027["front_static_frac"], "static_camber_deg_team": TEAM_CAMBER_DEG,
            "static_camber_deg_model": STATIC_CAMBER_DEG,
            "rack_travel_mm": RACK_TRAVEL_MM, "planned_diff_preload_drive_coast": PLANNED_DIFF,
        },
        "balance": balance,
        "lock_at_rack_travel": lock_check(v19_table, rows, ggv.wheelbase),
        "steering_fix": fix,
        "mass_cg": variant_summary(mass, ("variant", "radius_m")),
        "first_principles_optimum": optimum_check(nominal_car, rows),
        "track_minimum_curvature_line": {
            "track": line["track"], "length_m": round(line["length_m"], 1),
            "corners_under_15m": len(corners),
            "corners_under_6m": sum(1 for _, r in corners if r < 6.0),
            "corners_under_4_5m": sum(1 for _, r in corners if r < 4.5),
            "corners": [{"turn_deg": round(math.degrees(t), 1), "min_radius_m": round(r, 2)} for t, r in corners],
            "exit_accel_g": [round(exit_g, 3), EXIT_ACCEL_LOW_G],
            "entry_decel_g": [round(entry_g, 3), ENTRY_DECEL_LOW_G],
            "gain_per_lap_s_range": lap_gain_s(corners, rows, (exit_g, EXIT_ACCEL_LOW_G), (entry_g, ENTRY_DECEL_LOW_G)),
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("ackermann_pct_at_rack", "front_v19_geometry", "lock_at_3p5m_limit", "best_constant_curve_per_case")}, indent=2))


if __name__ == "__main__":
    main()
