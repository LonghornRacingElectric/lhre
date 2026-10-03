import argparse
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
PACKAGING_LIMIT_PCT = 42
ACKERMANN_PCT = (-50, -25, 0, 25, PACKAGING_LIMIT_PCT, 50, 75, 100)
LKY_CASES = {"lky_1": 1.0, "lky_scaled": MU_SCALE}
SLIP_SIDES = {"pos": 1.0, "neg": -1.0}
LLTD_OFFSETS = (-0.10, 0.0, 0.10)
NOMINAL = ("lky_1", "pos", 0.0)
GRIP_PROBE = 1.01
DRAG_FRACTION = 0.8
RACK_MM = np.arange(0.0, 60.5, 0.5)
REPORT_RACK_MM = (10.0, 20.0, 30.0)
BUMP_MM = 25.0
BRAKE_G = (0.3, 0.5)
BRAKE_BIAS_NOMINAL = 0.65
BRAKE_RADII_M = (3.5, 4.5)
BRAKE_CASES = (
    ("ellipse", "lky_1", 0.0), ("ellipse", "lky_1", 0.10),
    ("ellipse", "lky_scaled", 0.0), ("ellipse", "lky_scaled", 0.10),
    ("slip_norm", "lky_1", 0.0),
)
FX_MAX_FZ_N = np.arange(0.0, 2525.0, 25.0)
DRIVE_G = (0.0, 0.2)
DRIVE_RADII_M = (3.5, 4.5)
TOE_OUT_DEG = (-1.0, -0.5, 0.0, 0.5, 1.0)
TOE_RADII_M = (3.5, 4.5)
TOE_ACKERMANN_PCT = tuple(range(0, 101, 10))
REGEN_BIAS = (0.65, 0.55)
MASS_CASES = (
    ("mass +10 kg", "mass", 10.0), ("mass -10 kg", "mass", -10.0),
    ("CG +20 mm", "cg_height", 0.020), ("CG -20 mm", "cg_height", -0.020),
    ("front +3 pts", "front_static_frac", 0.03), ("front -3 pts", "front_static_frac", -0.03),
    ("camber -1 deg", "camber", None),
)
LB_TO_KG = 0.45359237
TEAM_2027 = {"mass": (430.0 + 150.0) * LB_TO_KG, "cg_height": 11.0 * 0.0254, "front_static_frac": 0.45}
STATIC_CAMBER_DEG = 0.0
TEAM_CAMBER_DEG = -1.0
RACK_TRAVEL_MM = 31.75
BALANCE_RADIUS_M = 15.0
STEER_FIX_TARGETS_PCT = (0.0, float(PACKAGING_LIMIT_PCT), 50.0, 70.0)
STEER_RESERVE = 0.9
PLANNED_DIFF = (5.0, 0.60, 0.35)
LAP_CONFIG = "_3_StandardSim/LapTimeEval/lap_time_eval_config.yml"
CORNER_RADIUS_MAX_M = 15.0
EXIT_ACCEL_LOW_G = 0.4
ENTRY_DECEL_LOW_G = 0.5
GGV_FIELDS = ("mass", "wheelbase", "cg_height", "track_front", "track_rear", "front_static_frac", "lltd", "max_drive_force")

BASELINE = "Front v20"
FRONT_V20_IN = {
    "lower_fore_i_m": [6.356, 8.610, 3.937],
    "lower_aft_i_m": [-3.211, 8.610, 3.937],
    "upper_fore_i_m": [6.533, 10.628, 8.268],
    "upper_aft_i_m": [-3.308, 10.628, 8.268],
    "lower_o_m": [-0.102, 23.072, 4.869],
    "upper_o_m": [0.401, 22.369, 10.797],
    "tie_o_m": [3.377, 22.473, 7.871],
}
FRONT_V20_M = {k: [0.0254 * c for c in v] for k, v in FRONT_V20_IN.items()} | {"wheel_center_m": [0.0, 0.622300, 0.199930]}
RACK_PICKUP_V20_M = [0.0254 * c for c in (2.866, 9.676, 6.087)]

RADIUS_COLORS = ("#0d366b", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef")
BRAKE_COLORS = ("#f09a70", "#eb6834", "#a8421a")
CAR_COLORS = {BASELINE: "#2a78d6", "Orion": "#eb6834"}
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


def with_front_v20(vehicle, tie_o_dy_m=0.0, tie_o_dx_m=0.0, rack_dy_m=0.0, rack_dz_m=0.0):
    v20 = copy.deepcopy(vehicle)
    v20["front"]["suspension"].update(copy.deepcopy(FRONT_V20_M))
    v20["front"]["suspension"]["tie_o_m"][0] += tie_o_dx_m
    v20["front"]["suspension"]["tie_o_m"][1] += tie_o_dy_m
    v20["front"]["steering"]["rack_pickup_m"] = [
        RACK_PICKUP_V20_M[0], RACK_PICKUP_V20_M[1] + rack_dy_m, RACK_PICKUP_V20_M[2] + rack_dz_m,
    ]
    return v20


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
                        "probe": (lky_name, side_name, lltd_offset) == NOMINAL,
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
            if bias != biases[0] and (lky_name, lltd_offset) != (NOMINAL[0], NOMINAL[2]):
                continue
            for brake_g in BRAKE_G:
                for radius in BRAKE_RADII_M:
                    cases.append({
                        "kind": "braking", "car": car, "curves": braking_curves, "outer_max": outer_max,
                        "radius": radius, "lon": Longitudinal(brake_g, bias=bias),
                        "probe": model == "ellipse" and (lky_name, lltd_offset) == (NOMINAL[0], NOMINAL[2]),
                        "labels": {
                            "combined_slip": model, "tire_case": lky_name, "lltd_front": round(car.lltd, 4),
                            "lltd_offset": lltd_offset, "brake_bias_front": bias, "brake_g": brake_g,
                            "radius_m": radius,
                        },
                    })
    tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[NOMINAL[0]]}
    car = Car(ggv, (tire,) * 4, ggv.lltd + NOMINAL[2])
    for diff_name, (preload, drive_lock, _, kinetic) in diffs.items():
        diff = (preload, drive_lock, kinetic)
        for drive_g in DRIVE_G:
            for radius in DRIVE_RADII_M:
                cases.append({
                    "kind": "drive", "car": car, "curves": braking_curves, "outer_max": outer_max,
                    "radius": radius, "lon": Longitudinal(-drive_g, diff=diff, wheel_radius=rear_radius),
                    "probe": diff_name != "open",
                    "labels": {"diff": diff_name, "drive_g": drive_g, "radius_m": radius},
                })
    car = Car(ggv, (tire,) * 4, ggv.lltd + NOMINAL[2])
    toe_curves = {BASELINE: curves[BASELINE]}
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
            for brake_g in BRAKE_G:
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
    return cases


def cost(case):
    if case["kind"] in ("braking", "drive") and case["car"].combined != "ellipse":
        return 0
    return {"track": 0, "fix": 0, "main": 1, "braking": 3, "drive": 3, "regen": 3, "toe": 4, "mass": 4}[case["kind"]]


def solve_case(case):
    if case["kind"] == "main":
        return solve_main_case(case)
    if case["kind"] in ("braking", "drive", "toe", "regen", "mass"):
        return solve_braking_case(case)
    if case["kind"] == "track":
        return minimum_curvature_line()
    return steering_fix(case["vehicle"], case["target_deg"], case["target_pct"], case["track"], case["wheelbase"])


def solve_main_case(case):
    car, curves, outer_max, radius, side = case["car"], case["curves"], case["outer_max"], case["radius"], case["side"]
    exit_g = exit_accel_g(car, case["max_drive_force"])
    rows, ref_ay = [], None
    for name, curve in sorted(curves.items(), key=lambda item: item[0] != BASELINE):
        labels = {**case["labels"], "curve": name}
        limit = limit_ay(car, curve, radius, side, outer_max)
        if limit is None:
            if name == BASELINE:
                raise RuntimeError(f"{BASELINE} has no limit for {labels}")
            rows.append({**labels, "ay_max_g": float("nan"), "ay_change_pct": float("nan")})
            continue
        (beta, outer), ay = limit
        s = state(car, curve, radius, side, beta, outer, ay)
        if name == BASELINE:
            ref_ay = ay
        front_gain = rear_gain = float("nan")
        drag = partial = None
        if case["probe"]:
            front_gain = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, side, outer_max, ay)
            rear_gain = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, side, outer_max, ay)
            partial = trim(car, curve, radius, side, DRAG_FRACTION * ref_ay, outer_max)
        if partial is not None:
            drag = state(car, curve, radius, side, *partial, DRAG_FRACTION * ref_ay)["drive_n"]
        peaks = [peak_lateral_n(t, fz, side) for t, fz in zip(car.tires, s["loads"])]
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
            "limiting_axle": limiting_axle(front_gain, rear_gain) if case["probe"] else None,
            "drive_n_at_80pct_ref_ay": drag,
            "turn_180_delta_s": turn_time_delta_s(radius, ref_ay, ay, exit_g),
            "exit_accel_g": exit_g,
        })
    return rows


def solve_braking_case(case):
    car, curves, outer_max, radius, lon = case["car"], case["curves"], case["outer_max"], case["radius"], case["lon"]
    rows, ref_ay = [], None
    for name, curve in sorted(curves.items(), key=lambda item: item[0] != BASELINE):
        labels = {**case["labels"], "curve": name}
        limit = limit_ay(car, curve, radius, 1.0, outer_max, lon)
        if limit is None:
            if name == BASELINE:
                raise RuntimeError(f"{BASELINE} has no limit for {labels}")
            rows.append({**labels, "ay_max_g": float("nan"), "ay_change_pct": float("nan")})
            continue
        z, ay = limit
        if name == BASELINE:
            ref_ay = ay
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
    nominal = sorted((r for r in rows if is_nominal(r) and r["curve"] == BASELINE), key=lambda r: r["radius_m"])
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
        return with_front_v20(vehicle, d[1] / 1000.0, d[0] / 1000.0, d[2] / 1000.0, d[3] / 1000.0)

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
    base = with_front_v20(vehicle)
    table, corner = steer_table(fixed)
    solved = bool(solved and len(table) > 0 and table[-1, 0] >= RACK_TRAVEL_MM)
    suspension = fixed["front"]["suspension"]
    result = {
        "target_ackermann_pct": target_pct,
        "target_mean_steer_deg": round(target_deg, 2),
        "target_rack_mm": round(fix_rack, 2),
        "solved": solved,
        "tie_o_shift_mm": {"x_forward": round(res.x[0], 1), "y_outboard": round(res.x[1], 1)},
        "rack_pickup_shift_mm": {"y_outboard": round(res.x[2], 1), "z_up": round(res.x[3], 1)},
        "tie_o_new_mm": [round(1000.0 * v, 1) for v in suspension["tie_o_m"]],
        "tie_o_inboard_of_wheel_center_plane_mm": round(1000.0 * (suspension["wheel_center_m"][1] - suspension["tie_o_m"][1]), 1),
        "arm_offset_from_kingpin_mm": {"front_v20": arm_offset_mm(base), "fix": arm_offset_mm(fixed)},
    }
    if not solved:
        return result
    return result | {
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
        "front_v20_inner_wheel_at_travel": lock_geometry(base, RACK_TRAVEL_MM),
    }


def axle_lock_g(car, bias, axle):
    share = bias if axle == 0 else 1.0 - bias

    def holds(decel_g):
        shift = decel_g * car.cg_height / car.wheelbase
        fz = car.mass * G * (car.front_frac + shift if axle == 0 else 1.0 - car.front_frac - shift) / 2
        return fz > 0.0 and share * car.mass * G * decel_g / 2 <= car.peak(car.fx_max_n[-1], 2 * axle, fz)

    low, high = 0.0, 4.0
    if holds(high):
        return math.inf
    for _ in range(50):
        mid = 0.5 * (low + high)
        low, high = (mid, high) if holds(mid) else (low, mid)
    return low


def braking_limit_g(car, bias):
    front, rear = axle_lock_g(car, bias, 0), axle_lock_g(car, bias, 1)
    ideal = minimize_scalar(
        lambda b: -min(axle_lock_g(car, b, 0), axle_lock_g(car, b, 1)), bounds=(0.4, 0.9), method="bounded",
        options={"xatol": 1e-4},
    )
    return {
        "front_lock_g": round(front, 3), "rear_lock_g": round(rear, 3), "limit_g": round(min(front, rear), 3),
        "locks_first": "front" if front < rear else "rear",
        "ideal_front_bias": round(float(ideal.x), 3), "ideal_limit_g": round(-float(ideal.fun), 3),
    }


def lap_gain_s(corners, rows, exit_g, entry_g):
    nominal = [r for r in rows if is_nominal(r)]

    def ay_at(curve, radius):
        values = [next(r["ay_max_g"] for r in nominal if r["radius_m"] == rr and r["curve"] == curve) for rr in RADII_M]
        return float(np.interp(radius, RADII_M, values))

    gains = {}
    for curve in (f"{p:+d}%" for p in ACKERMANN_PCT if p > 0):
        if not all(math.isfinite(r["ay_max_g"]) for r in nominal if r["curve"] in (BASELINE, curve)):
            gains[curve] = None
            continue
        bounds = []
        for exit_a, entry_a in zip(exit_g, entry_g):
            total = 0.0
            for theta, radius in corners:
                r = max(radius, RADII_M[0])
                v0, v1 = math.sqrt(ay_at(BASELINE, r) * G * r), math.sqrt(ay_at(curve, r) * G * r)
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
        subset = [r for r in rows if key(r) == k and r["curve"] != BASELINE]
        winner = max(subset, key=lambda r: np.nan_to_num(r["ay_max_g"], nan=-1.0))
        best.setdefault(label(winner), []).append(winner["curve"])
    return {"nominal": nominal, "best_constant_curve_per_case": best}


def drive_summary(rows):
    out = {}
    for r in rows:
        key = f"{r['diff']}, {r['drive_g']:g} g, {r['radius_m']:g} m"
        out.setdefault(key, {})[r["curve"]] = {
            "ay_max_g": round(r["ay_max_g"], 3), "ay_change_pct": round(r["ay_change_pct"], 2),
            "limiting_axle": r.get("limiting_axle"),
            "wheelspin_limited": None if r.get("max_wheel_use") is None else bool(r["max_wheel_use"] > 0.97),
        }
    return out


def variant_summary(rows, keys):
    out = {}
    for r in rows:
        entry = out.setdefault(", ".join(f"{k} {r[k]}" for k in keys), {"change_vs_v20_pct": {}})
        entry["change_vs_v20_pct"][r["curve"]] = round(r["ay_change_pct"], 2)
        if r["curve"] == BASELINE:
            entry["v20_ay_max_g"] = round(r["ay_max_g"], 3)
        if r.get("limiting_axle"):
            entry.setdefault("limiting_axle", {})[r["curve"]] = r["limiting_axle"]
    for entry in out.values():
        changes = {k: v for k, v in entry["change_vs_v20_pct"].items() if k != BASELINE and v == v}
        entry["best_curve"] = max(changes, key=changes.get)
    return out


def toe_summary(rows):
    ref = {r["radius_m"]: r["ay_max_g"] for r in rows if r["toe_out_deg"] == 0.0 and r["curve"] == BASELINE}
    out = {}
    for r in rows:
        key = f"toe-out {r['toe_out_deg']:g} deg, {r['radius_m']:g} m"
        entry = out.setdefault(key, {"change_vs_v20_zero_toe_pct": {}})
        entry["change_vs_v20_zero_toe_pct"][r["curve"]] = round(100.0 * (r["ay_max_g"] / ref[r["radius_m"]] - 1.0), 2)
    for entry in out.values():
        changes = {k: v for k, v in entry["change_vs_v20_zero_toe_pct"].items() if k != BASELINE}
        entry["best_curve"] = max(changes, key=changes.get)
        entry["best_change_pct"] = changes[entry["best_curve"]]
    return out


def signed(x, unit="%", digits=1):
    x = round(x, digits) + 0.0
    return (f"{x:+.{digits}f}" if x else f"{0:.{digits}f}").replace("-", "−") + unit


def span(lo, hi, unit="", digits=1):
    return f"{signed(lo, '', digits)} to {signed(hi, unit, digits)}"


def table(header, rows):
    return "\n".join(["| " + " | ".join(header) + " |", "| " + " | ".join("-" * len(h) for h in header) + " |"]
                     + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]) + "\n"


def linkage_row(name, fix, arm_mm, plane_mm, lock):
    if fix is None:
        move = rack = "–"
    else:
        dx, dy = fix["tie_o_shift_mm"]["x_forward"], fix["tie_o_shift_mm"]["y_outboard"]
        move = f"{abs(dx):.0f} mm {'rearward' if dx < 0 else 'forward'}, {abs(dy):.0f} mm {'outboard' if dy > 0 else 'inboard'}"
        ry, rz = fix["rack_pickup_shift_mm"]["y_outboard"], fix["rack_pickup_shift_mm"]["z_up"]
        rack = f"{abs(ry):.0f} mm {'outboard' if ry > 0 else 'inboard'}" + (f", {abs(rz):.0f} mm {'up' if rz > 0 else 'down'}" if abs(rz) >= 0.5 else "")
        if not fix["solved"]:
            return [name, move, "not solved", "–", rack, "–", "–"]
    toe = lock["bump_toe_change_deg"]
    return [
        name, move, f"{plane_mm:.0f} mm", f"{arm_mm:.0f} mm", rack, f"{lock['toggle_margin_deg']:.0f}°",
        f"{signed(toe['-25mm'], '°')} / {signed(toe['+25mm'], '°')}",
    ]


def write_readme_parts(out, s, rows):
    nominal, spread, fix = s["nominal"], s["ay_change_pct_range_all_cases"], s["steering_fix"]
    parts = {}
    pack = f"+{PACKAGING_LIMIT_PCT}%"
    shown = (pack, "+50%", "+75%", "+100%")
    apex = []
    for radius in RADII_M:
        key = f"{radius:g}m"
        apex.append([f"{radius:g} m"] + [f"{signed(nominal[key][c]['ay_change_pct'])} ({span(*spread[key][c])})" for c in shown]
                    + [signed(nominal[key][pack]["turn_180_delta_ms"], " ms", 0)])
    parts["apex"] = table(["R", *shown, f"Time per 180° turn, {pack}"], apex)

    plane = 1000.0 * (FRONT_V20_M["wheel_center_m"][1] - FRONT_V20_M["tie_o_m"][1])
    link = [linkage_row(BASELINE, None, fix[0]["arm_offset_from_kingpin_mm"]["front_v20"], plane, fix[0]["front_v20_inner_wheel_at_travel"])]
    for f in fix:
        link.append(linkage_row(f"{f['target_ackermann_pct']:+.0f}%".replace("+0%", "0%"), f, f["arm_offset_from_kingpin_mm"]["fix"],
                                f["tie_o_inboard_of_wheel_center_plane_mm"], f.get("inner_wheel_at_travel")))
    parts["linkage"] = table(["Option", "Tie rod outer move", "To wheel center plane", "Arm to kingpin", "Rack pickup move",
                              "Toggle margin at inner lock", "Bump toe at inner lock, ±25 mm"], link)

    optimum = s["first_principles_optimum"]
    parts["optimum"] = table(["R", "Front-limited optimum", "Solver optimum (10% steps)", "+75% limited by"], [
        [f"{r:g} m", f"{optimum[f'{r:g}m']['yaw_weighted_optimum_pct']:.0f}%", s["toe"][f"toe-out 0 deg, {r:g} m"]["best_curve"],
         nominal[f"{r:g}m"]["+75%"]["limiting_axle"]] for r in TOE_RADII_M
    ])

    limits = s["team_2027_inputs"]["straight_braking_limit"]
    parts["bias"] = table(["Front bias", "Locks first", "Straight-line limit"], [
        [f"{100 * float(b):.0f}%", v["locks_first"], f"{v['limit_g']:.2f} g"] for b, v in limits.items()
    ])

    brake = s["braking"]["nominal"]
    parts["braking"] = table(["R, braking", "+25%", pack, "+75%", "+100%"], [
        [f"{r:g} m, {g:g} g"] + [
            f"{signed(brake[f'slip_norm, bias {BRAKE_BIAS_NOMINAL:.2f}, {g:g} g, {r:g} m'][c]['ay_change_pct'], '')} / "
            f"{signed(brake[f'ellipse, bias {BRAKE_BIAS_NOMINAL:.2f}, {g:g} g, {r:g} m'][c]['ay_change_pct'])}"
            for c in ("+25%", pack, "+75%", "+100%")
        ] for r in BRAKE_RADII_M for g in BRAKE_G
    ])

    def drive_cell(entry):
        if any(v.get("wheelspin_limited") for v in entry.values()):
            return "spin"
        return " / ".join(signed(entry[c]["ay_change_pct"], "") for c in ("+50%", "+75%", "+100%")) + "%"

    parts["throttle"] = table(["R, drive", "Open diff", "Orion LSD", "Planned LSD"], [
        [f"{r:g} m, {g:g} g"] + [drive_cell(s["drive"][f"{d}, {g:g} g, {r:g} m"]) for d in ("open", "orion", "planned")]
        for g in DRIVE_G for r in DRIVE_RADII_M
    ])

    regen = s["regen_through_diff"]
    parts["regen"] = table(["Diff, front share, braking", "3.5 m", "4.5 m"], [
        [f"{d}, {100 * share:.0f}%, {g:g} g"] + [
            f"{signed(e['change_vs_v20_pct']['+50%'])}, best {e['best_curve']}, {e['limiting_axle'][BASELINE]}-limited"
            for e in (regen[f"diff {d}, front_share {share}, brake_g {g}, radius_m {r}"] for r in BRAKE_RADII_M)
        ] for d in ("orion", "planned") for share in REGEN_BIAS for g in BRAKE_G
    ])

    parts["toe"] = table(["Total toe-out", "3.5 m best", "4.5 m best"], [
        [signed(t, "°") + (" (toe-in)" if t < 0 and t == min(TOE_OUT_DEG) else "")]
        + [s["toe"][f"toe-out {t:g} deg, {r:g} m"]["best_curve"] for r in TOE_RADII_M] for t in TOE_OUT_DEG
    ])

    gains = s["track_minimum_curvature_line"]["gain_per_lap_s_range"]
    parts["lap"] = table([f"Linkage", f"Time saved per lap vs {BASELINE}"], [
        [c, "not solved" if v is None else f"{-v[0]:.2f} to {-v[1]:.2f} s"] for c, v in gains.items()
    ])

    for name, text in parts.items():
        (out / f"{name}.md").write_text(text, encoding="utf-8")

    pack_brake = [brake[f"{m}, bias {BRAKE_BIAS_NOMINAL:.2f}, {g:g} g, {r:g} m"][pack]["ay_change_pct"]
                  for m in ("ellipse", "slip_norm") for g in BRAKE_G for r in BRAKE_RADII_M]
    lock = s["lock_at_rack_travel"]
    ack = s["ackermann_pct_at_rack"][BASELINE]
    front_limited = [r for r in RADII_M if nominal[f"{r:g}m"][BASELINE]["limiting_axle"] == "front"]
    pro = ("+25%", "+50%", "+75%", "+100%")
    open_cost = max(-nominal[f"{r:g}m"][c]["ay_change_pct"] for r in RADII_M if r >= 8.0 for c in pro)
    sensitivity = [brake[f"{m}, bias {float(b):.2f}, 0.5 g, 3.5 m"][c]["ay_change_pct"]
                   for m in ("ellipse", "slip_norm") for b in limits if float(b) != BRAKE_BIAS_NOMINAL for c in ("+25%", "+50%")]
    model_gap = max(abs(brake[k][c]["ay_change_pct"] - brake[k.replace("slip_norm", "ellipse", 1)][c]["ay_change_pct"])
                    for k in brake if k.startswith("slip_norm") for c in pro)
    mass_gap = max(abs(v["change_vs_v20_pct"][c] - nominal[k.rsplit("radius_m ", 1)[1].rstrip("0").rstrip(".") + "m"][c]["ay_change_pct"])
                   for k, v in s["mass_cg"].items() for c in pro[1:])
    drag = {c: nominal["3.5m"][c]["drive_n_at_80pct"] for c in (BASELINE, "+50%", "+75%")}
    track = s["track_minimum_curvature_line"]
    ideal = limits[f"{BRAKE_BIAS_NOMINAL:.2f}"]
    values = {
        "lltd_front_pct": 100.0 * s["vehicle"]["lltd_front"],
        "v20_ackermann_pct": f"{signed(max(ack.values()))} to {signed(min(ack.values()))}",
        "steer_at_travel_deg": lock["mean_steer_at_travel_deg"],
        "steer_needed_3p5_deg": lock["mean_steer_needed_at_limit_deg"]["3.5m"],
        "steer_needed_4p5_deg": lock["mean_steer_needed_at_limit_deg"]["4.5m"],
        "tightest_radius_m": lock["tightest_cg_radius_at_limit_m"],
        "rear_axle_walking_m": lock["rear_axle_radius_walking_m"],
        "outer_wheel_walking_m": lock["outer_front_wheel_center_radius_walking_m"],
        "rack_needed_mm": lock["rack_needed_for_3p5m_at_limit_mm"],
        "arm_offset_mm": fix[0]["arm_offset_from_kingpin_mm"]["front_v20"],
        "fix_rack_mm": fix[0]["target_rack_mm"],
        "steer_0_deg": fix[0]["target_mean_steer_deg"],
        "steer_50_deg": next(f["target_mean_steer_deg"] for f in fix if f["target_ackermann_pct"] == 50.0),
        "front_limited_to_m": f"{max(front_limited):g} m" if front_limited else "none",
        "open_corner_cost_pct": max(open_cost, 0.0),
        "ideal_bias_pct": 100.0 * ideal["ideal_front_bias"],
        "ideal_limit_g": ideal["ideal_limit_g"],
        "bias_sensitivity_gain": span(min(sensitivity), max(sensitivity), "%"),
        "brake_model_gap_pts": model_gap,
        "points_per_deg_toe_3p5": s["first_principles_optimum"]["3.5m"]["points_per_deg_toe_out"],
        "points_per_deg_toe_4p5": s["first_principles_optimum"]["4.5m"]["points_per_deg_toe_out"],
        "mass_gap_pts": mass_gap,
        "track_length_m": track["length_m"],
        "corners_under_15m": track["corners_under_15m"],
        "corners_under_6m": track["corners_under_6m"],
        "tightest_corner_m": min(c["min_radius_m"] for c in track["corners"]),
        "exit_g": track["exit_accel_g"][0],
        "entry_g": track["entry_decel_g"][0],
        "drag_v20_n": drag[BASELINE], "drag_50_n": drag["+50%"], "drag_75_n": drag["+75%"],
        "drag_8m_gap_n": max(nominal["8m"][c]["drive_n_at_80pct"] for c in drag) - min(nominal["8m"][c]["drive_n_at_80pct"] for c in drag),
        "skidpad_pct": max(max(abs(v) for v in spread["8m"][c]) for c in pro),
        "gain_75_3p5": span(*spread["3.5m"]["+75%"], "%"),
        "pack_pct": PACKAGING_LIMIT_PCT,
        "pack_apex_3p5": nominal["3.5m"][pack]["ay_change_pct"],
        "pack_apex_4p5": nominal["4.5m"][pack]["ay_change_pct"],
        "pack_share_of_best_3p5": 100.0 * nominal["3.5m"][pack]["ay_change_pct"] / max(nominal["3.5m"][c]["ay_change_pct"] for c in pro),
        "pack_lap_s": "not solved" if gains.get(pack) is None else f"{-gains[pack][0]:.2f} to {-gains[pack][1]:.2f} s",
        "best_lap_s": max((-v[0], c) for c, v in gains.items() if v)[1],
        "pack_brake": span(min(pack_brake), max(pack_brake), "%"),
        "pack_plane_mm": next(f["tie_o_inboard_of_wheel_center_plane_mm"] for f in fix if f["target_ackermann_pct"] == PACKAGING_LIMIT_PCT),
        "pack_steer_deg": next(f["target_mean_steer_deg"] for f in fix if f["target_ackermann_pct"] == PACKAGING_LIMIT_PCT),
    }
    (out / "readme.json").write_text(json.dumps(finite(values), indent=2), encoding="utf-8")


def finite(value):
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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


def plot_grip(path, rows, v20_pct, braking, bias):
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.0, 4.2), facecolor=SURFACE)
    pct = np.array(ACKERMANN_PCT, dtype=float)
    plot_braking(right, rows, braking, bias)
    for radius, color in zip(RADII_M, RADIUS_COLORS):
        def series(key, select):
            picked = [next(r.get(key) for r in rows if select(r) and r["radius_m"] == radius and r["curve"] == f"{p:+d}%") for p in ACKERMANN_PCT]
            return np.array([np.nan if v is None else v for v in picked], dtype=float)

        nominal = series("ay_change_pct", is_nominal)
        cases = {(r["tire_case"], r["slip_side"], r["lltd_offset"]) for r in rows}
        spread = np.array([series("ay_change_pct", lambda r, c=c: (r["tire_case"], r["slip_side"], r["lltd_offset"]) == c) for c in cases])
        left.fill_between(pct, np.nanmin(spread, axis=0), np.nanmax(spread, axis=0), color=color, alpha=0.15, lw=0)
        left.plot(pct, nominal, color=color, lw=2, marker="o", ms=4, label=f"R = {radius:g} m")
        left.plot([v20_pct[radius]], [0.0], marker="D", ms=7, color=color, mec=SURFACE, mew=1.5)
    left.axhline(0.0, color=MUTED, lw=0.8)
    left.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    left.set_ylabel("Max lateral g change vs Front v20 (%)", color=INK)
    left.set_title("Apex grip by corner radius (diamond = Front v20)", color=INK, loc="left", fontsize=11)
    for ax in (left, right):
        style(ax)
    left.legend(frameon=False, fontsize=8, labelcolor=INK)
    right.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_braking(ax, rows, braking, bias):
    names = [n for n in (f"{p:+d}%" for p in ACKERMANN_PCT) if any(r["curve"] == n for r in braking)]
    pct = np.array([float(n.rstrip("%")) for n in names])
    apex = {r["curve"]: r["ay_change_pct"] for r in rows if is_nominal(r) and r["radius_m"] == BRAKE_RADII_M[0]}
    ax.plot(pct, [apex[n] for n in names], color=BRAKE_COLORS[0], lw=2, marker="o", ms=4, label="0 g")
    for brake_g, color in zip(BRAKE_G, BRAKE_COLORS[1:]):
        for model, style_ in (("slip_norm", "-"), ("ellipse", "--")):
            picked = {
                r["curve"]: r["ay_change_pct"] for r in braking
                if r["combined_slip"] == model and r["tire_case"] == NOMINAL[0] and r["lltd_offset"] == NOMINAL[2]
                and r["brake_bias_front"] == bias and r["brake_g"] == brake_g and r["radius_m"] == BRAKE_RADII_M[0]
            }
            if len(picked) < len(names):
                continue
            label = f"{brake_g:g} g" if model == "slip_norm" else None
            ax.plot(pct, [picked[n] for n in names], color=color, lw=2, ls=style_, marker="o", ms=4, label=label)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    ax.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    ax.set_ylabel("Max lateral g change vs Front v20 (%)", color=INK)
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


def apply_args(argv=None):
    global MU_SCALE, LKY_CASES, ACKERMANN_PCT, STEER_FIX_TARGETS_PCT, BRAKE_BIAS_NOMINAL, REGEN_BIAS
    parser = argparse.ArgumentParser()
    parser.add_argument("--mu", type=float, default=MU_SCALE, help="tire grip scale, LMUY = LMUX")
    parser.add_argument("--ackermann", type=int, nargs="*", default=[], help="extra Ackermann %% to sweep and solve a linkage for")
    parser.add_argument("--bias", type=float, default=BRAKE_BIAS_NOMINAL, help="nominal front brake bias, 0 to 1")
    args = parser.parse_args(argv)
    MU_SCALE, BRAKE_BIAS_NOMINAL = args.mu, args.bias
    LKY_CASES = {**LKY_CASES, "lky_scaled": MU_SCALE}
    REGEN_BIAS = (BRAKE_BIAS_NOMINAL,) + REGEN_BIAS[1:]
    ACKERMANN_PCT = tuple(sorted(set(ACKERMANN_PCT) | set(args.ackermann)))
    STEER_FIX_TARGETS_PCT = tuple(sorted(set(STEER_FIX_TARGETS_PCT) | {float(p) for p in args.ackermann if p >= 0}))


def main():
    apply_args()
    out = Path(os.environ["OUT_DIR"])
    orion = load_yaml(Path(os.environ["BOBSIM_VEHICLE"]))
    v20 = with_front_v20(orion)
    (out / "vehicle_front_v20.yml").write_text(yaml.safe_dump(v20, sort_keys=False), encoding="utf-8")

    projection = project_vehicle_yaml(v20)
    ggv = types.SimpleNamespace(**{f: getattr(projection.ggv, f) for f in GGV_FIELDS})
    for field, value in TEAM_2027.items():
        setattr(ggv, field, value)
    orion_ggv = project_vehicle_yaml(orion).ggv
    orion_table, _ = steer_table(orion)
    v20_table, corner = steer_table(v20)
    origin = corner.initial_point_set()

    with (out / "steering_curves.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["car", "rack_mm", "inner_deg", "outer_deg", "ackermann_pct"])
        for name, table, g in (("Orion", orion_table, orion_ggv), (BASELINE, v20_table, ggv)):
            for rack, inner, outer in table:
                pct = ackermann_pct(inner, outer, g.track_front, g.wheelbase) if rack >= 2.0 else float("nan")
                writer.writerow([name, rack, math.degrees(inner), math.degrees(outer), pct])

    curves = {BASELINE: TableCurve(v20_table)}
    curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in ACKERMANN_PCT})
    outer_max = float(v20_table[-1, 2])
    tir = parse_tir(tire_templates_root(v20) / f"{v20['front']['tire']['template']}.tir")
    ggv.lltd, balance = balanced_lltd(ggv, tir, curves[BASELINE], outer_max)
    nominal_tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[NOMINAL[0]]}
    nominal_car = Car(ggv, (nominal_tire,) * 4, ggv.lltd)
    steer_targets = {
        pct: needed_mean_steer_deg(nominal_car, ConstantAckermann(pct, ggv.track_front, ggv.wheelbase), RADII_M[0], outer_max)
        for pct in STEER_FIX_TARGETS_PCT
    }
    dl = v20["powertrain"]["pDriveline"]
    diffs = {
        "open": (0.0, 0.0, 0.0, 0.0),
        "orion": (dl["diff_T_preload"], dl["diff_lockFractionAccel"], dl["diff_lockFractionDecel"], dl["diff_kineticFrictionRatio"]),
        "planned": PLANNED_DIFF + (dl["diff_kineticFrictionRatio"],),
    }
    cases = build_cases(
        ggv, tir, curves, outer_max, (BRAKE_BIAS_NOMINAL, float(v20["brake"]["front_bias"])), orion,
        diffs, float(v20["rear"]["wheel"]["radius_m"]), steer_targets,
    )
    order = sorted(range(len(cases)), key=lambda i: (cost(cases[i]), i))
    solved = map_cases(solve_case, [cases[i] for i in order])
    results = [None] * len(cases)
    for i, result in zip(order, solved):
        results[i] = result
    rows = [row for case, result in zip(cases, results) if case["kind"] == "main" for row in result]
    braking = [row for case, result in zip(cases, results) if case["kind"] == "braking" for row in result]
    drive = [row for case, result in zip(cases, results) if case["kind"] == "drive" for row in result]
    toe = [row for case, result in zip(cases, results) if case["kind"] == "toe" for row in result]
    regen = [row for case, result in zip(cases, results) if case["kind"] == "regen" for row in result]
    line = next(result for case, result in zip(cases, results) if case["kind"] == "track")
    fix = [result for case, result in zip(cases, results) if case["kind"] == "fix"]
    corners = track_corners(line)
    exit_g = next(r["exit_accel_g"] for r in rows if is_nominal(r))
    braking_limit = {f"{b:.2f}": braking_limit_g(nominal_car, b) for b in (BRAKE_BIAS_NOMINAL, float(v20["brake"]["front_bias"]))}
    entry_g = braking_limit[f"{BRAKE_BIAS_NOMINAL:.2f}"]["limit_g"]
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

    v20_rows = [r for r in rows if r["curve"] == BASELINE]
    nominal_v20 = {r["radius_m"]: r for r in v20_rows if is_nominal(r)}
    v20_pct = {radius: r["ackermann_pct_at_limit"] for radius, r in nominal_v20.items()}
    steer_band = (
        min(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_v20.values() if r["radius_m"] <= 8.0),
        max(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_v20.values() if r["radius_m"] <= 8.0),
    )
    tables = {"Orion": orion_table, BASELINE: v20_table}
    geometry = {"Orion": (orion_ggv.track_front, orion_ggv.wheelbase), BASELINE: (ggv.track_front, ggv.wheelbase)}
    plot_curves(out / "ackermann_curves.png", tables, geometry, steer_band)
    plot_grip(out / "grip_vs_ackermann.png", rows, v20_pct, braking, BRAKE_BIAS_NOMINAL)

    tight = nominal_v20[RADII_M[0]]
    lock_rack_mm = float(np.interp(math.radians(tight["inner_deg"]), v20_table[:, 1], v20_table[:, 0]))
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
        subset = [r for r in rows if (r["tire_case"], r["slip_side"], r["lltd_offset"], r["radius_m"]) == key and r["curve"] != BASELINE]
        best.setdefault(f"{key[3]:g}m", []).append(max(subset, key=lambda r: np.nan_to_num(r["ay_max_g"], nan=-1.0))["curve"])

    summary = {
        "vehicle": {
            "note": "Front v20 steering hardpoints, team 2027 mass and CG, balanced LLTD, Orion rear and tire",
            "mass_kg": round(ggv.mass, 2), "cg_height_m": round(ggv.cg_height, 4),
            "front_static_frac": round(ggv.front_static_frac, 4), "wheelbase_m": round(ggv.wheelbase, 4),
            "track_front_m": round(ggv.track_front, 4), "track_rear_m": round(ggv.track_rear, 4),
            "lltd_front": round(ggv.lltd, 4), "lltd_source": f"balanced at R = {BALANCE_RADIUS_M:g} m",
        },
        "tire": {"template": v20["front"]["tire"]["template"], "LMUY_LMUX": MU_SCALE, "LKY_cases": LKY_CASES},
        "front_v20_geometry": {
            "caster_deg": round(corner.caster_deg(origin), 2), "kpi_deg": round(corner.kpi_deg(origin), 2),
            "bump_toe_deg": {f"{j:+g}mm": round(bump_toe_deg(corner, j), 3) for j in (-BUMP_MM, BUMP_MM)},
        },
        "ackermann_pct_at_rack": {
            name: {f"{r:g}mm": round(float(ackermann_pct(at_rack(t, r, 1), at_rack(t, r, 2), *geometry[name])), 1) for r in REPORT_RACK_MM}
            for name, t in tables.items()
        },
        "lock_at_3p5m_limit": {
            "inner_deg": round(tight["inner_deg"], 2), "outer_deg": round(tight["outer_deg"], 2),
            "rack_mm": round(lock_rack_mm, 1), "rack_sweep_max_mm": float(v20_table[-1, 0]),
        },
        "exit_accel_g": {r["tire_case"]: round(r["exit_accel_g"], 3) for r in v20_rows},
        "nominal_case": dict(zip(("tire_case", "slip_side", "lltd_offset"), NOMINAL)),
        "nominal": table_rows,
        "ay_change_pct_range_all_cases": spread,
        "best_constant_curve_per_case": {k: sorted(v) for k, v in best.items()},
        "braking": braking_summary(braking),
        "drive": drive_summary(drive),
        "toe": toe_summary(toe),
        "regen_through_diff": variant_summary(regen, ("diff", "front_share", "brake_g", "radius_m")),
        "team_2027_inputs": {
            "mass_kg": round(TEAM_2027["mass"], 1), "cg_height_m": round(TEAM_2027["cg_height"], 4),
            "front_static_frac": TEAM_2027["front_static_frac"], "static_camber_deg_team": TEAM_CAMBER_DEG,
            "static_camber_deg_model": STATIC_CAMBER_DEG,
            "rack_travel_mm": RACK_TRAVEL_MM, "planned_diff_preload_drive_coast": PLANNED_DIFF,
            "brake_bias_front_nominal": BRAKE_BIAS_NOMINAL,
            "straight_braking_limit": braking_limit,
        },
        "balance": balance,
        "lock_at_rack_travel": lock_check(v20_table, rows, ggv.wheelbase),
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
    (out / "summary.json").write_text(json.dumps(finite(summary), indent=2, allow_nan=False), encoding="utf-8")
    write_readme_parts(out, json.loads(json.dumps(finite(summary))), rows)
    print(json.dumps({k: summary[k] for k in ("ackermann_pct_at_rack", "front_v20_geometry", "lock_at_3p5m_limit", "best_constant_curve_per_case")}, indent=2))


if __name__ == "__main__":
    main()
