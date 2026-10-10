import argparse
import copy
import hashlib
import csv
import json
import math
import os
import types
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
import numpy as np
import yaml
from scipy.optimize import brentq, least_squares, minimize_scalar

from _0_Utils.kin_py.kinematics import CornerKinematics
from _0_Utils.lap_sim.qss import GGVMap, solve_qss_lap
from _0_Utils.lap_sim.racing_line import optimize_racing_line
from _0_Utils.lap_sim.track import RacingLine, TrackCorridor
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
RACK_MM = np.arange(0.0, 60.5, 0.5)
REPORT_RACK_MM = (10.0, 20.0, 31.75)
BUMP_MM = 25.0
BRAKE_G = (0.3, 0.5)
BRAKE_BIAS_NOMINAL = 0.65
BRAKE_RADII_M = (3.5, 4.5)
BRAKE_MODELS = ("ellipse", "slip_norm")
BRAKE_CURVES = ("Front V33", "-50%", "-25%", "+25%", "+50%", "+75%", "+100%")
FX_MAX_FZ_N = np.arange(0.0, 2525.0, 25.0)
TOE_OUT_DEG = (-1.0, -0.5, 0.0, 0.5, 1.0)
TOE_RADII_M = (3.5, 4.5)
TOE_ACKERMANN_PCT = tuple(range(0, 101, 10))
MASS_CURVES = ("Front V33", "-50%", "+50%", "+75%")
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
ANTI_LINKAGE_PCT = -40
STEER_FIX_TARGETS_PCT = (float(ANTI_LINKAGE_PCT), 0.0, 70.0)
STEER_RESERVE = 0.9
PLANNED_DIFF = (5.0, 0.60)
LAP_CONFIG = "_3_StandardSim/LapTimeEval/lap_time_eval_config.yml"
CORNER_RADIUS_MAX_M = 15.0
MU_SENSITIVITY = 0.75
MAP_RADII_M = (3.5, 4.5, 6.0, 8.0, 11.0, 15.0)
MAP_AX_G = (-1.2, -0.9, -0.6, -0.3, 0.0, 0.15, 0.3, 0.6, 0.9)
MAP_PCTS = (-50, -25, 25, 50, 75, 100)
BEST_MIN_GAIN_PCT = 0.1
DERIV_RADII_M = (3.5, 4.5, 8.0)
LIMIT_CURVES = ("-50%", "+0%", "+50%", "Front V33")
DERIV_FRACTION = 0.9
QSS_SPEEDS_MPS = tuple(np.arange(3.0, 16.5, 0.5)) + (17.0, 18.0, 19.0, 20.0, 22.0, 25.0, 30.0, 35.0)
QSS_AY_STEP_G = 0.001
CIRCLE_CHECK_RADIUS_M = 8.0
CIRCLE_CHECK_TOL = 0.005
CONVERGENCE_CURVES = ("-50%", "+50%", "+75%", "+100%", "Front V33")
SENSITIVITY_CURVES = ("-50%", "+50%")
LOCK_MARGIN_MIN_DEG = 0.5
COVERAGE_STEP = 5
COVERAGE_DEMANDS = (1.0, 0.98, 0.95)
COVERAGE_CURVES = ("+0%", "-50%", "+50%")
SKIDPAD_RADIUS_M = 9.125
LAP_RADIUS_BANDS_M = (6.0, 15.0)
GGV_FIELDS = ("mass", "wheelbase", "cg_height", "track_front", "track_rear", "front_static_frac", "lltd", "max_drive_force")

BASELINE = "+0%"
DESIGN = "Front V33"
FRONT_SHK_MM = {
    "lower_fore_i_m": (110.431, 198.700, 76.000),
    "lower_aft_i_m": (-100.273, 198.700, 76.000),
    "lower_o_m": (2.000, 598.739, 104.495),
    "upper_fore_i_m": (165.945, 239.958, 217.000),
    "upper_aft_i_m": (-104.470, 241.260, 196.410),
    "upper_o_m": (-6.000, 580.866, 295.000),
    "tie_o_m": (80.000, 605.350, 199.930),
    "wheel_center_m": (0.000, 635.000, 199.930),
}
RACK_PICKUP_SHK_MM = (25.277, 240.652, 140.010)
FRONT_M = {k: [c / 1000.0 for c in v] for k, v in FRONT_SHK_MM.items()}
RACK_PICKUP_M = [c / 1000.0 for c in RACK_PICKUP_SHK_MM]

RADIUS_COLORS = ("#0d366b", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef")
GAIN_MAP = LinearSegmentedColormap.from_list("gain", ("#e34948", "#f0efec", "#2a78d6"))
CAR_COLORS = {DESIGN: "#2a78d6", "Orion": "#eb6834"}
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


def with_front(vehicle, tie_o_dy_m=0.0, tie_o_dx_m=0.0, rack_dy_m=0.0, rack_dz_m=0.0):
    base = copy.deepcopy(vehicle)
    base["front"]["suspension"].update(copy.deepcopy(FRONT_M))
    base["front"]["suspension"]["tie_o_m"][0] += tie_o_dx_m
    base["front"]["suspension"]["tie_o_m"][1] += tie_o_dy_m
    base["front"]["steering"]["rack_pickup_m"] = [
        RACK_PICKUP_M[0], RACK_PICKUP_M[1] + rack_dy_m, RACK_PICKUP_M[2] + rack_dz_m,
    ]
    return base


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
    def __init__(self, decel_g, bias=None, diff=None, wheel_radius=None):
        self.decel_g, self.bias, self.diff, self.wheel_radius = decel_g, bias, diff, wheel_radius

    def wheel_fx(self, total_n, car=None, loads=None, speeds=None):
        if self.diff is not None:
            return (0.0, 0.0) + lsd_split(total_n, self.diff, self.wheel_radius, car, loads[2:], speeds)
        front, rear = -self.bias * total_n / 2, -(1.0 - self.bias) * total_n / 2
        return (front, front, rear, rear)


def lsd_split(total_n, diff, radius, car, loads, speeds):
    preload, lock, kinetic = diff
    torque = total_n * radius
    capacity = kinetic * (2.0 * preload + lock * abs(torque))
    tire = car.tires[2]
    fz_in, fz_out = loads
    v_in, v_out = speeds

    def wheel_fx(wheel_speed):
        return tuple(_mf52_fx_pure(tire, fz, (wheel_speed - v) / v, 0.0) for fz, v in ((fz_in, v_in), (fz_out, v_out)))

    kappa_peak = car.peak(car.kappa_peak[1 if total_n >= 0.0 else -1], 2, 0.5 * (fz_in + fz_out))
    top = max(v_in, v_out) * (1.0 + kappa_peak) if total_n >= 0.0 else min(v_in, v_out) * (1.0 - kappa_peak)
    low, high = (min(v_in, v_out), top) if total_n >= 0.0 else (top, max(v_in, v_out))
    if (sum(wheel_fx(low)) - total_n) * (sum(wheel_fx(high)) - total_n) > 0.0:
        return (torque / 2 / radius + math.copysign(capacity, torque) / 2 / radius, torque / 2 / radius - math.copysign(capacity, torque) / 2 / radius)
    locked = wheel_fx(brentq(lambda w: sum(wheel_fx(w)) - total_n, low, high, xtol=1e-6))
    if abs(locked[0] - locked[1]) * radius <= capacity:
        return locked
    slip = math.copysign(capacity, locked[0] - locked[1]) / 2 / radius
    return (torque / 2 / radius + slip, torque / 2 / radius - slip)


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


def state(car, curve, radius, side, beta, outer, ay_g, lon=None, lon_n=0.0):
    speed = math.sqrt(ay_g * G * radius)
    yaw_rate = speed / radius
    steer = (curve(outer), outer, 0.0, 0.0)
    decel_g = lon.decel_g if lon else 0.0
    ax_body = -decel_g * math.cos(beta) - ay_g * math.sin(beta)
    ay_body = -decel_g * math.sin(beta) + ay_g * math.cos(beta)
    loads = car.loads(ay_body, -ax_body)
    rear_speeds = tuple(math.hypot(speed * math.cos(beta) - yaw_rate * y, speed * math.sin(beta) + yaw_rate * x) for x, y in car.corners[2:])
    wheel_fx = lon.wheel_fx(lon_n, car, loads, rear_speeds) if lon else (0.0, 0.0, 0.0, 0.0)
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
    return {
        "speed": speed, "steer": steer, "loads": loads, "alphas": alphas, "forces": forces,
        "brake_use": uses, "wheel_fx": wheel_fx, "fx": fx, "fy": fy, "mz": mz,
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
    for start in (0.02, low, 0.3, 0.6, 1.0):
        best = trim(car, curve, radius, side, start, outer_max, lon=lon)
        if best is not None:
            low = start
            break
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
    def surplus(ax_g):
        fz = car.mass * G * (1.0 - car.front_frac + ax_g * car.cg_height / car.wheelbase) / 2
        return 2.0 * car.peak(car.fx_max_n[1], 2, fz) - car.mass * G * ax_g

    traction = brentq(surplus, 0.05, 3.0, xtol=1e-6)
    return min(traction, max_drive_force / (car.mass * G))


def build_cases(ggv, tir, curves, outer_max, biases, orion, steer_targets, nominal_car):
    cases = []
    for lky_name, lky in LKY_CASES.items():
        tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": lky}
        for lltd_offset in LLTD_OFFSETS:
            car = Car(ggv, (tire,) * 4, ggv.lltd + lltd_offset)
            for side_name, side in SLIP_SIDES.items():
                for radius in RADII_M:
                    cases.append({
                        "kind": "main", "car": car, "curves": curves, "outer_max": outer_max,
                        "radius": radius, "side": side,
                        "probe": (lky_name, side_name, lltd_offset) == NOMINAL,
                        "labels": {
                            "tire_case": lky_name, "slip_side": side_name, "lltd_front": round(car.lltd, 4),
                            "lltd_offset": lltd_offset, "radius_m": radius,
                        },
                    })
    brake_curves = {name: curves[name] for name in (BASELINE, *BRAKE_CURVES)}
    for model in BRAKE_MODELS:
        car = copy.copy(nominal_car)
        car.combined = model
        for bias in biases:
            for brake_g in BRAKE_G:
                for radius in BRAKE_RADII_M:
                    cases.append({
                        "kind": "braking", "car": car, "curves": brake_curves, "outer_max": outer_max,
                        "radius": radius, "lon": Longitudinal(brake_g, bias=bias),
                        "labels": {"combined_slip": model, "brake_bias_front": bias, "brake_g": brake_g, "radius_m": radius},
                    })
    toe_curves = {BASELINE: curves[BASELINE]}
    toe_curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in TOE_ACKERMANN_PCT})
    for toe in TOE_OUT_DEG:
        for radius in TOE_RADII_M:
            cases.append({
                "kind": "toe", "car": nominal_car, "curves": {k: ToeCurve(v, toe) for k, v in toe_curves.items()},
                "outer_max": outer_max, "radius": radius, "lon": None,
                "labels": {"toe_out_deg": toe, "radius_m": radius},
            })
    mass_curves = {name: curves[name] for name in (BASELINE, *MASS_CURVES)}
    tires = nominal_car.tires
    for name, field, delta in MASS_CASES:
        variant = types.SimpleNamespace(**{f: getattr(ggv, f) for f in GGV_FIELDS})
        if field == "camber":
            car = Car(variant, tires, nominal_car.lltd, camber_deg=TEAM_CAMBER_DEG)
        else:
            setattr(variant, field, getattr(variant, field) + delta)
            car = Car(variant, tires, nominal_car.lltd)
        for radius in TOE_RADII_M:
            cases.append({
                "kind": "mass", "car": car, "curves": mass_curves, "outer_max": outer_max, "radius": radius,
                "lon": None, "labels": {"variant": name, "radius_m": radius},
            })
    cases.append({"kind": "track"})
    for target, target_deg in steer_targets.items():
        cases.append({
            "kind": "fix", "vehicle": orion, "target_deg": target_deg, "target_pct": target,
            "track": ggv.track_front, "wheelbase": ggv.wheelbase,
        })
    return cases


def cost(case):
    if case["kind"] == "braking" and case["car"].combined != "ellipse":
        return 0
    return {"track": 0, "fix": 0, "main": 1, "braking": 3, "toe": 4, "mass": 4}[case["kind"]]


def solve_case(case):
    if case["kind"] == "main":
        return solve_main_case(case)
    if case["kind"] in ("braking", "toe", "mass"):
        return solve_braking_case(case)
    if case["kind"] == "track":
        return minimum_curvature_line()
    if case["kind"] == "map":
        return solve_map_case(case)
    if case["kind"] == "qss":
        return solve_qss_case(case)
    if case["kind"] == "coverage":
        return coverage_case(case)
    return steering_fix(case["vehicle"], case["target_deg"], case["target_pct"], case["track"], case["wheelbase"])


def solve_main_case(case):
    car, curves, outer_max, radius, side = case["car"], case["curves"], case["outer_max"], case["radius"], case["side"]
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
        if case["probe"]:
            front_gain = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, side, outer_max, ay)
            rear_gain = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, side, outer_max, ay)
        inner = s["steer"][0]
        rows.append({
            **labels, "ay_max_g": ay, "ay_change_pct": 100.0 * (ay / ref_ay - 1.0),
            "speed_mps": s["speed"], "beta_deg": math.degrees(beta),
            "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
            "ackermann_pct_at_limit": float(ackermann_pct(inner, outer, car.track_front, car.wheelbase)),
            **{f"alpha_{c}_deg": math.degrees(a) for c, a in zip(("fl", "fr", "rl", "rr"), s["alphas"])},
            **{f"fz_{c}_n": f for c, f in zip(("fl", "fr", "rl", "rr"), s["loads"])},
            "ay_gain_pct_per_1pct_front_grip": 100.0 * front_gain,
            "ay_gain_pct_per_1pct_rear_grip": 100.0 * rear_gain,
            "limiting_axle": limiting_axle(front_gain, rear_gain) if case["probe"] else None,
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
        rows.append(row)
    return rows


def minimum_curvature_line():
    config = load_yaml(repo_root() / LAP_CONFIG)["track"]
    corridor = TrackCorridor.from_csv(repo_root() / config["boundary_csv"])
    ay = np.linspace(0.0, 15.0, 16)
    ax = np.sqrt(np.maximum(15.0**2 - ay**2, 0.0))
    ggv = GGVMap.from_arrays((5.0, 40.0), ay, np.vstack([ax, ax]), np.vstack([-ax, -ax]))
    result = optimize_racing_line(
        corridor, ggv, mode="minimum_curvature", vehicle_width_m=config["vehicle_width_m"],
        safety_margin_m=config["safety_margin_m"], sample_step_m=config["sample_step_m"],
    )
    return {
        "track": config["boundary_csv"], "curvature": result.line.curvature_per_m,
        "segment": result.line.segment_length_m, "length_m": float(result.line.track_length_m), "line": result.line,
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


def balanced_lltd(ggv, tir, curve, outer_max, mu=None):
    mu = MU_SCALE if mu is None else mu
    tire = {**tir, "LMUY": mu, "LMUX": mu, "LKY": LKY_CASES[NOMINAL[0]]}
    car = Car(ggv, (tire,) * 4, 0.5)

    def negative_ay(lltd):
        car.lltd = lltd
        limit = limit_ay(car, curve, BALANCE_RADIUS_M, 1.0, outer_max)
        return 0.0 if limit is None else -limit[1]

    return minimize_scalar(negative_ay, bounds=(0.2, 0.8), method="bounded", options={"xatol": 1e-3}).x


def needed_mean_steer_deg(car, curve, radius, outer_max):
    (beta, outer), ay = limit_ay(car, curve, radius, 1.0, outer_max)
    return math.degrees(0.5 * (curve(outer) + outer))


def lock_check(table, rows):
    nominal = sorted((r for r in rows if is_nominal(r) and r["curve"] == DESIGN), key=lambda r: r["radius_m"])
    radii = [r["radius_m"] for r in nominal]
    need = [0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal]
    mean = np.degrees(0.5 * (table[:, 1] + table[:, 2]))
    reach = float(np.interp(RACK_TRAVEL_MM, table[:, 0], mean))
    return {
        "rack_travel_mm": RACK_TRAVEL_MM,
        "mean_steer_at_travel_deg": round(reach, 1),
        "mean_steer_needed_at_limit_deg": {f"{r:g}m": round(n, 1) for r, n in zip(radii, need)},
        "tightest_cg_radius_at_limit_m": round(float(np.interp(-reach, [-n for n in need], radii)), 2),
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
        return with_front(vehicle, d[1] / 1000.0, d[0] / 1000.0, d[2] / 1000.0, d[3] / 1000.0)

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
        bounds=([-60.0, -70.0, -60.0, -60.0], [10.0, 50.0, 60.0, 60.0]), diff_step=1e-2, xtol=1e-8,
    )
    e = errors(res.x)
    solved = e is not None and abs(e[0]) < 0.1 and abs(e[1]) < 1.0 and max(abs(e[2]), abs(e[3])) < 0.02
    fixed = build(res.x)
    table, _ = steer_table(fixed)
    solved = bool(solved and len(table) > 0 and table[-1, 0] >= RACK_TRAVEL_MM)
    suspension = fixed["front"]["suspension"]
    result = {
        "target_ackermann_pct": target_pct,
        "target_rack_mm": round(fix_rack, 2),
        "solved": solved,
        "tie_o_shift_mm": {"x_forward": round(res.x[0], 1), "y_outboard": round(res.x[1], 1)},
        "rack_pickup_shift_mm": {"y_outboard": round(res.x[2], 1), "z_up": round(res.x[3], 1)},
        "tie_o_inboard_of_wheel_center_plane_mm": round(1000.0 * (suspension["wheel_center_m"][1] - suspension["tie_o_m"][1]), 1),
        "arm_offset_mm": arm_offset_mm(fixed),
        "table": table.tolist(),
    }
    if solved:
        result["inner_wheel_at_travel"] = lock_geometry(fixed, RACK_TRAVEL_MM)
    return result


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


def pct_label(pct):
    return f"{pct:+.0f}%".replace("+0%", "0%")


def ax_label(ax_g):
    return f"{-ax_g:g} g braking" if ax_g < 0 else (f"{ax_g:g} g drive" if ax_g > 0 else "steady")


def yaw_derivatives(car, curve, radius, outer_max, ay_g, lon, step=math.radians(0.05)):
    z = trim(car, curve, radius, 1.0, ay_g, outer_max, lon=lon)
    if z is None:
        return {}
    beta, outer, lon_n = z[0], z[1], (z[2] if lon else 0.0)
    norm = car.mass * G * car.wheelbase

    def mz(b, o):
        return state(car, curve, radius, 1.0, b, o, ay_g, lon, lon_n)["mz"] / norm

    mean = lambda o: 0.5 * (curve(o) + o)
    n_beta = (mz(beta + step, outer) - mz(beta - step, outer)) / (2.0 * step)
    n_delta = (mz(beta, outer + step) - mz(beta, outer - step)) / (mean(outer + step) - mean(outer - step))
    return {
        "deriv_ay_g": ay_g, "n_beta_per_deg": math.radians(1.0) * n_beta,
        "n_delta_per_deg": math.radians(1.0) * n_delta, "deriv_mean_steer_deg": math.degrees(mean(outer)),
    }


def solve_map_case(case):
    car, curves, radius, lon, outer_max = case["car"], case["curves"], case["radius"], case["lon"], case["outer_max"]
    rows, ref = [], None
    for name, curve in curves.items():
        row = {**case["labels"], "curve": name, "ay_max_g": float("nan"), "ay_change_pct": float("nan")}
        limit = limit_ay(car, curve, radius, 1.0, outer_max, lon)
        if limit is not None:
            row["ay_max_g"] = limit[1]
            outer = limit[0][1]
            row["inner_deg"], row["outer_deg"] = math.degrees(curve(outer)), math.degrees(outer)
            row["lock_margin_deg"] = math.degrees(outer_max - outer)
            if name == BASELINE:
                ref = limit[1]
            if ref:
                row["ay_change_pct"] = 100.0 * (limit[1] / ref - 1.0)
            if case["probe"] and name == BASELINE:
                front = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, 1.0, outer_max, limit[1], lon)
                rear = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, 1.0, outer_max, limit[1], lon)
                row["limiting_axle"] = limiting_axle(front, rear)
            if case["deriv"]:
                row.update(yaw_derivatives(car, curve, radius, outer_max, DERIV_FRACTION * limit[1], lon))
        rows.append(row)
    return rows


def check_map(rows, curve, brake_g, drive_g):
    grid = {(r["radius_m"], r["ax_g"]): r["ay_max_g"] for r in rows if r["curve"] == curve}
    caps = []
    for radius in MAP_RADII_M:
        if not math.isfinite(grid[(radius, 0.0)]):
            raise RuntimeError(f"{curve} has no steady limit at {radius} m")
        for sign, endpoint in ((-1.0, brake_g), (1.0, drive_g)):
            levels = sorted((x for x in MAP_AX_G if x * sign > 0), key=abs)
            finite = [math.isfinite(grid[(radius, x)]) for x in levels]
            if any(f and not all(finite[:k]) for k, f in enumerate(finite)):
                caps.append({"curve": curve, "radius_m": radius, "side": "drive" if sign > 0 else "brake", "gap": True})
            if not all(finite):
                top = abs(levels[finite.index(False) - 1]) if finite.index(False) > 0 else 0.0
                caps.append({"curve": curve, "radius_m": radius, "side": "drive" if sign > 0 else "brake",
                             "cap_g": top, "endpoint_g": round(endpoint, 3)})
    return caps


def envelope_ggv(rows, curve, brake_g, drive_g, mass, power_w, refine=False):
    radii = np.asarray(MAP_RADII_M)
    grid = {(r["radius_m"], r["ax_g"]): r["ay_max_g"] for r in rows if r["curve"] == curve}
    if not all(math.isfinite(grid[(r, 0.0)]) for r in MAP_RADII_M):
        raise RuntimeError(f"{curve} has no steady limit at some radius")

    def at(radius, ax):
        return float(np.interp(radius, radii, [grid[(r, ax)] for r in MAP_RADII_M]))

    def side(radius, steady, sign, endpoint):
        points, top = [(steady, 0.0)], endpoint
        for x in sorted((x for x in MAP_AX_G if x * sign > 0), key=abs):
            ay = at(radius, x)
            if not math.isfinite(ay):
                top = points[-1][1]
                break
            points.append((min(ay, steady), abs(x)))
        return points + [(0.0, top)]

    def boundary(points, ay):
        points = sorted(((a, x) for a, x in points if math.isfinite(a)), key=lambda point: (point[0], -point[1]))
        ays = np.asarray([a for a, _ in points])
        limit = np.minimum.accumulate(np.asarray([x for _, x in points]))
        keep = np.r_[True, np.diff(ays) > 1e-9]
        return float(np.interp(ay, ays[keep], limit[keep]))

    def steady_at(speed, ay):
        return at(speed**2 / (ay * G), 0.0)

    speeds = np.asarray(QSS_SPEEDS_MPS, dtype=float)
    step = QSS_AY_STEP_G
    if refine:
        speeds, step = np.unique(np.r_[speeds, 0.5 * (speeds[1:] + speeds[:-1])]), step / 2.0
    exact = [brentq(lambda ay, v=v: ay - steady_at(v, ay), 1e-3, 3.0, xtol=1e-9) for v in speeds]
    ay_grid = np.unique(np.r_[np.arange(0.0, 2.4, step), exact])
    accel = np.full((len(speeds), ay_grid.size), np.nan)
    brake = np.full_like(accel, np.nan)
    for i, speed in enumerate(speeds):
        for j, ay in enumerate(ay_grid):
            radius = speed**2 / (ay * G) if ay > 0.0 else math.inf
            steady = at(radius, 0.0)
            if ay > steady + 1e-9:
                break
            brake[i, j] = -G * boundary(side(radius, steady, -1.0, brake_g), ay)
            accel[i, j] = min(G * boundary(side(radius, steady, 1.0, drive_g), ay), power_w / (mass * speed))
    return GGVMap.from_arrays(speeds, ay_grid * G, accel, brake)


def solve_qss_case(case):
    ggv = envelope_ggv(case["rows"], case["labels"]["curve"], case["brake_g"], case["drive_g"], case["mass"], case["power_w"],
                       case.get("refine", False))
    lap = solve_qss_lap(case["line"], ggv)
    if not lap.converged:
        raise RuntimeError(f"QSS lap did not converge for {case['labels']}")
    return {**case["labels"], "refine": case.get("refine", False), "lap_time_s": lap.lap_time_s, "segment_time_s": lap.segment_time_s,
            "ax_mps2": lap.longitudinal_acceleration_mps2, "speed_mps": lap.speed_mps}


def cap_fill_cases(qss_cases, op_rows, caps):
    if not any(c.get("mu") == MU_SCALE for c in caps):
        return []
    top = {}
    for r in op_rows:
        if r["mu"] == MU_SCALE and math.isfinite(r["ay_max_g"]):
            top[(r["radius_m"], r["ax_g"])] = max(top.get((r["radius_m"], r["ax_g"]), 0.0), r["ay_max_g"])
    rows = [r if math.isfinite(r["ay_max_g"]) else {**r, "ay_max_g": top.get((r["radius_m"], r["ax_g"]), float("nan"))}
            for r in op_rows if r["mu"] == MU_SCALE]
    return [{**c, "rows": rows, "labels": {**c["labels"], "fill": True}} for c in qss_cases if c["labels"]["mu"] == MU_SCALE]


def cap_check_result(nominal_laps, fill_laps):
    if not fill_laps:
        return {"laws": {}, "max_change_ms": 0.0}
    base = {k: next(r["lap_time_s"] for r in laps if r["mu"] == MU_SCALE and r["curve"] == BASELINE)
            for k, laps in (("capped", nominal_laps), ("filled", fill_laps))}
    change = {}
    for r in fill_laps:
        if r["curve"] == BASELINE:
            continue
        capped = next(n["lap_time_s"] for n in nominal_laps if n["mu"] == MU_SCALE and n["curve"] == r["curve"]) - base["capped"]
        change[r["curve"]] = round(1000.0 * (r["lap_time_s"] - base["filled"] - capped), 2)
    return {"laws": change, "max_change_ms": round(max(abs(v) for v in change.values()), 2)}


def circle_check(rows, brake_g, drive_g, mass, power_w):
    count = 400
    angle = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    step = 2.0 * math.pi * CIRCLE_CHECK_RADIUS_M / count
    line = RacingLine(
        station_m=np.arange(count) * step, x_m=CIRCLE_CHECK_RADIUS_M * np.cos(angle), y_m=CIRCLE_CHECK_RADIUS_M * np.sin(angle),
        heading_rad=angle + math.pi / 2.0, curvature_per_m=np.full(count, 1.0 / CIRCLE_CHECK_RADIUS_M),
        segment_length_m=np.full(count, step), gate_offsets_m=np.zeros(count), track_length_m=count * step,
    )
    lap = solve_qss_lap(line, envelope_ggv(rows, BASELINE, brake_g, drive_g, mass, power_w))
    ay = next(r["ay_max_g"] for r in rows if r["curve"] == BASELINE and r["ax_g"] == 0.0 and r["radius_m"] == CIRCLE_CHECK_RADIUS_M)
    expected = 2.0 * math.pi * CIRCLE_CHECK_RADIUS_M / math.sqrt(ay * G * CIRCLE_CHECK_RADIUS_M)
    error = lap.lap_time_s / expected - 1.0
    if abs(error) > CIRCLE_CHECK_TOL:
        raise RuntimeError(f"QSS circle check failed: {lap.lap_time_s:.4f} s against {expected:.4f} s")
    return {"radius_m": CIRCLE_CHECK_RADIUS_M, "qss_s": round(lap.lap_time_s, 4), "hand_s": round(expected, 4),
            "error_pct": round(100.0 * error, 3)}


def hairpin_time_s(rows, curve, radius):
    ay = next(r["ay_max_g"] for r in rows if r["curve"] == curve and r["ax_g"] == 0.0 and r["radius_m"] == radius)
    return math.pi * radius / math.sqrt(ay * G * radius)


def best_laws(op_rows, mu):
    cell = {(r["radius_m"], r["ax_g"], r["curve"]): r for r in op_rows if r["mu"] == mu}
    out = {}
    for radius in MAP_RADII_M:
        for ax in MAP_AX_G:
            laws = [(cell[(radius, ax, f"{p:+d}%")], p) for p in MAP_PCTS]
            if not math.isfinite(cell[(radius, ax, BASELINE)]["ay_max_g"]):
                feasible = [p for r, p in laws if math.isfinite(r["ay_max_g"])]
                out[(radius, ax)] = (min(feasible), math.inf) if feasible else (None, float("nan"))
                continue
            gain, pct = max((r["ay_change_pct"], p) for r, p in laws if math.isfinite(r["ay_change_pct"]))
            out[(radius, ax)] = (pct, gain) if gain > BEST_MIN_GAIN_PCT else (0, 0.0)
    return out


def best_text(pct, gain, sep=" "):
    if pct is None:
        return "no grip"
    if pct == 0:
        return "parallel"
    if math.isinf(gain):
        return f"only {pct_label(pct)}{sep}and up"
    return f"{pct_label(pct)}{sep}({signed(gain)})"


def coverage_case(case):
    car, curve, outer_max, bias = case["car"], case["curve"], case["outer_max"], case["bias"]
    out = []
    for radius, ax_g, ay_g, dt, phase in case["points"]:
        for demand in COVERAGE_DEMANDS:
            ax, ay = demand * ax_g, demand * ay_g
            if phase == "braking":
                lon = Longitudinal(-ax, bias=bias)
            else:
                lon = Longitudinal(-ax, diff=case["drive_diff"], wheel_radius=case["rear_radius"])
            r = min(radius, 1000.0)
            z = trim(car, curve, r, 1.0, ay, outer_max, lon=lon)
            if z is None:
                guess = trim(car, curve, r, 1.0, 0.5 * ay, outer_max, lon=lon)
                z = trim(car, curve, r, 1.0, ay, outer_max, guess, lon) if guess is not None else None
            out.append((phase, demand, dt, z is not None))
    return out


def trim_coverage(lap, curvature, car, curve, outer_max, drive_diff, rear_radius, bias):
    points, count = [], len(curvature)
    for i in range(0, count, COVERAGE_STEP):
        ax = lap["ax_mps2"][i] / G
        phase = "braking" if ax < -0.05 else ("exit" if ax > 0.05 else "steady")
        j = (i + 1) % count if phase == "braking" else i
        ay = lap["speed_mps"][j] ** 2 * abs(curvature[j]) / G
        if ay < 0.05:
            continue
        points.append((1.0 / abs(curvature[j]), ax, ay, lap["segment_time_s"][i], phase))
    chunks = [points[k::24] for k in range(24)]
    cases = [{"kind": "coverage", "car": car, "curve": curve, "outer_max": outer_max, "drive_diff": drive_diff,
              "rear_radius": rear_radius, "bias": bias, "points": chunk} for chunk in chunks if chunk]
    results = [r for chunk in map_cases(solve_case, cases) for r in chunk]
    out = {"points": len(points), "by_demand": {}}
    for demand in COVERAGE_DEMANDS:
        level = {}
        for phase in ("braking", "steady", "exit", "all"):
            sel = [(dt, ok) for p, d, dt, ok in results if d == demand and phase in (p, "all")]
            level[phase] = round(sum(dt for dt, ok in sel if ok) / max(sum(dt for dt, _ in sel), 1e-9), 3)
        out["by_demand"][f"{demand:g}"] = level
    return out


def clipped_braking(op_rows):
    steady = {(r["mu"], r["radius_m"], r["curve"]): r["ay_max_g"] for r in op_rows if r["ax_g"] == 0.0}
    excess = [(r["ay_max_g"] - steady[(r["mu"], r["radius_m"], r["curve"])], r) for r in op_rows if r["ax_g"] < 0.0]
    clipped = [(e, r) for e, r in excess if e > 0.0]
    top = max(clipped, key=lambda item: item[0]) if clipped else (0.0, {})
    return {
        "cells": len(clipped), "of": len(excess), "max_g": round(top[0], 3),
        "max_at": {k: top[1].get(k) for k in ("curve", "radius_m", "ax_g", "mu")},
        "by_curve": {c: round(max([e for e, r in clipped if r["curve"] == c], default=0.0), 3)
                     for c in dict.fromkeys(r["curve"] for r in op_rows)},
    }


def skidpad_time_s(rows, curve):
    steady = {r["radius_m"]: r["ay_max_g"] for r in rows if r["curve"] == curve and r["ax_g"] == 0.0}
    ay = float(np.interp(SKIDPAD_RADIUS_M, MAP_RADII_M, [steady[r] for r in MAP_RADII_M]))
    return 2.0 * math.pi * SKIDPAD_RADIUS_M / math.sqrt(ay * G * SKIDPAD_RADIUS_M)


def lap_phases(laps, curvature, op_rows):
    radius = 1.0 / np.maximum(np.abs(curvature), 1e-9)
    bands = np.digitize(radius, LAP_RADIUS_BANDS_M)
    band_names = [f"under {LAP_RADIUS_BANDS_M[0]:g} m", f"{LAP_RADIUS_BANDS_M[0]:g} to {LAP_RADIUS_BANDS_M[1]:g} m",
                  f"over {LAP_RADIUS_BANDS_M[1]:g} m"]
    out = {}
    for mu in sorted({lap["mu"] for lap in laps}):
        base = next(lap for lap in laps if lap["mu"] == mu and lap["curve"] == BASELINE)
        phase = np.where(base["ax_mps2"] < -0.05 * G, "braking", np.where(base["ax_mps2"] > 0.05 * G, "exit", "corner"))
        for lap in laps:
            if lap["mu"] != mu or lap["curve"] == BASELINE:
                continue
            delta = lap["segment_time_s"] - base["segment_time_s"]
            rows_mu = [r for r in op_rows if r["mu"] == mu]
            out.setdefault(f"{mu:g}", {})[lap["curve"]] = {
                "lap_time_s": round(lap["lap_time_s"], 3),
                "delta_s": round(lap["lap_time_s"] - base["lap_time_s"], 3),
                **{f"delta_{k}_s": round(float(delta[phase == k].sum()), 3) for k in ("braking", "corner", "exit")},
                **{f"delta_band_{i}_s": round(float(delta[bands == i].sum()), 3) for i in range(len(band_names))},
                "skidpad_delta_s": round(skidpad_time_s(rows_mu, lap["curve"]) - skidpad_time_s(rows_mu, BASELINE), 4),
            }
        out[f"{mu:g}"][BASELINE] = {
            "lap_time_s": round(base["lap_time_s"], 3),
            "skidpad_time_s": round(skidpad_time_s([r for r in op_rows if r["mu"] == mu], BASELINE), 3),
            "time_share_by_band": {n: round(float(base["segment_time_s"][bands == i].sum() / base["lap_time_s"]), 3)
                                   for i, n in enumerate(band_names)},
        }
    out["radius_bands"] = band_names
    return out


def optimum_check(car, rows):
    out = {}
    for radius in RADII_M[:4]:
        row = next(r for r in rows if is_nominal(r) and r["radius_m"] == radius and r["curve"] == "+75%")
        beta, speed = math.radians(row["beta_deg"]), row["speed_mps"]
        yaw = speed / radius
        path = [math.atan2(speed * math.sin(beta) + yaw * x, speed * math.cos(beta) - yaw * y) for x, y in car.corners[:2]]
        loads = (row["fz_fl_n"], row["fz_fr_n"])
        weighted = []
        for i, (theta, fz) in enumerate(zip(path, loads)):
            lever = car.corners[i][1]
            res = minimize_scalar(
                lambda a: -(car.wheelbase * math.cos(theta + a) + lever * math.sin(theta + a)) * lateral_n(car.tires[i], fz, a, 1.0, car.camber[i]),
                bounds=(0.0, 0.3), method="bounded", options={"xatol": 1e-7},
            )
            weighted.append(res.x)
        inner, outer = path[0] + weighted[0], path[1] + weighted[1]
        optimum = float(ackermann_pct(inner, outer, car.track_front, car.wheelbase))
        per_toe = float(ackermann_pct(inner + math.radians(1.0), outer, car.track_front, car.wheelbase)) - optimum
        out[f"{radius:g}m"] = {"yaw_weighted_optimum_pct": round(optimum, 1), "points_per_deg_toe_out": round(per_toe, 1)}
    return out


def limiting_axle(front_gain, rear_gain):
    return "front" if np.nan_to_num(front_gain, nan=-1.0) > np.nan_to_num(rear_gain, nan=-1.0) else "rear"


def max_change_gap(rows, ref_rows, match, curves):
    ref = {tuple(r[k] for k in match): r["ay_change_pct"] for r in ref_rows}
    gaps = [abs(r["ay_change_pct"] - ref[tuple(r[k] for k in match)]) for r in rows if r["curve"] in curves]
    return max(g for g in gaps if math.isfinite(g))


def toe_summary(rows):
    ref = {r["radius_m"]: r["ay_max_g"] for r in rows if r["toe_out_deg"] == 0.0 and r["curve"] == BASELINE}
    out = {}
    for r in rows:
        key = f"toe-out {r['toe_out_deg']:g} deg, {r['radius_m']:g} m"
        entry = out.setdefault(key, {"change_vs_base_zero_toe_pct": {}})
        entry["change_vs_base_zero_toe_pct"][r["curve"]] = round(100.0 * (r["ay_max_g"] / ref[r["radius_m"]] - 1.0), 2)
    for entry in out.values():
        changes = {k: v for k, v in entry["change_vs_base_zero_toe_pct"].items() if k != BASELINE}
        entry["best_curve"] = max(changes, key=changes.get)
    return out


def signed(x, unit="%", digits=1):
    if x is None or not math.isfinite(x):
        return "no grip"
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


def write_readme_parts(out, s, op_rows):
    nominal, spread, fix = s["nominal"], s["ay_change_pct_range_all_cases"], s["steering_fix"]
    laws = [f"{p:+d}%" for p in MAP_PCTS]
    parts = {}
    parts["apex"] = table(["R", *laws], [
        [f"{r:g} m"] + [f"{signed(nominal[f'{r:g}m'][c]['ay_change_pct'])} ({span(*spread[f'{r:g}m'][c])})" for c in laws]
        for r in RADII_M
    ])

    plane = 1000.0 * (FRONT_M["wheel_center_m"][1] - FRONT_M["tie_o_m"][1])
    front = s["front_base_linkage"]
    link = [linkage_row(DESIGN, None, front["arm_offset_mm"], plane, front["inner_wheel_at_travel"])]
    for f in fix:
        link.append(linkage_row(pct_label(f["target_ackermann_pct"]), f, f["arm_offset_mm"],
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

    parts["toe"] = table(["Total toe-out", "3.5 m best", "4.5 m best"], [
        [signed(t, "°") + (" (toe-in)" if t < 0 and t == min(TOE_OUT_DEG) else "")]
        + [s["toe"][f"toe-out {t:g} deg, {r:g} m"]["best_curve"] for r in TOE_RADII_M] for t in TOE_OUT_DEG
    ])

    cell = {(r["mu"], r["radius_m"], r["ax_g"], r["curve"]): r for r in op_rows}
    best = best_laws(op_rows, MU_SCALE)

    def best_cell(radius, ax):
        pct, gain = best[(radius, ax)]
        axle = cell[(MU_SCALE, radius, ax, BASELINE)].get("limiting_axle")
        return best_text(pct, gain) + (f" {axle[0].upper()}" if axle else "")

    parts["map"] = table(["Longitudinal", *(f"{r:g} m" for r in MAP_RADII_M)], [
        [ax_label(a)] + [best_cell(r, a) for r in MAP_RADII_M] for a in MAP_AX_G
    ])

    deriv = lambda r: f"{1000 * r['n_beta_per_deg']:+.1f} / {1000 * r['n_delta_per_deg']:.1f}".replace("-", "−")
    parts["limit"] = table(["R", *LIMIT_CURVES], [
        [f"{radius:g} m"] + [deriv(cell[(MU_SCALE, radius, 0.0, c)]) for c in LIMIT_CURVES] for radius in DERIV_RADII_M
    ])

    others = [c for c in s["operating_map"]["curves"] if c != BASELINE]
    nominal_rows = [r for r in op_rows if r["mu"] == MU_SCALE]
    parts["hairpin"] = table(["Option", *(f"{r:g} m" for r in TOE_RADII_M)], [
        [c] + [signed(1000 * (hairpin_time_s(nominal_rows, c, r) - hairpin_time_s(nominal_rows, BASELINE, r)), " ms", 0)
               for r in TOE_RADII_M] for c in others
    ])

    lock_rows = []
    for c in s["operating_map"]["curves"]:
        r = cell[(MU_SCALE, 3.5, 0.0, c)]
        mean = 0.5 * (r["inner_deg"] + r["outer_deg"])
        lock_rows.append([c, f"{mean:.1f}°", f"{r['inner_deg']:.1f}° / {r['outer_deg']:.1f}°"])
    parts["lock"] = table(["Option", "Mean steer at the 3.5 m limit", "Inner / outer"], lock_rows)

    sens = s["sensitivity"]
    parts["sensitivity"] = table(["Variant", "−50% lap", "+50% lap", "+50% at 3.5 m steady", "+50% at 3.5 m, hardest braking"], [
        [v, signed(sens[v]["-50%"]["delta_s"], " s", 2), signed(sens[v]["+50%"]["delta_s"], " s", 2),
         signed(sens[v]["+50%"]["steady_3p5_pct"]), signed(sens[v]["+50%"]["brake_3p5_pct"])] for v in sens
    ])

    laps = s["lap_qss"]
    bands = laps["radius_bands"]
    mus = [m for m in laps if m != "radius_bands"]
    parts["lap"] = table(
        ["Option", *(f"Endurance lap, grip {m}" for m in mus), *(f"Skidpad lap, grip {m}" for m in mus)],
        [[c] + [signed(laps[m][c]["delta_s"], " s", 2) for m in mus]
         + [signed(laps[m][c]["skidpad_delta_s"], " s", 3) for m in mus] for c in others],
    )
    share = laps[mus[0]][BASELINE]["time_share_by_band"]
    parts["where"] = table(
        ["Option", "Braking zones", "Steady corners", "Exits", *(f"Radius {b} ({100 * share[b]:.0f}% of lap)" for b in bands)],
        [[c] + [signed(laps[mus[0]][c][f"delta_{k}_s"], " s", 2) for k in ("braking", "corner", "exit")]
         + [signed(laps[mus[0]][c][f"delta_band_{i}_s"], " s", 2) for i in range(len(bands))] for c in others],
    )

    for name, text in parts.items():
        (out / f"{name}.md").write_text(text, encoding="utf-8")

    lock = s["lock_at_rack_travel"]
    ack = s["ackermann_pct_at_rack"][DESIGN]
    signs = [pct for pct, _ in best.values() if pct is not None]
    best_lap = {m: min(laws, key=lambda c: laps[m][c]["delta_s"]) for m in mus}
    band = s["grid_convergence"]["band_s"]
    tied = {m: [c for c in others if laps[m][c]["delta_s"] <= laps[m][best_lap[m]]["delta_s"] + band] for m in mus}
    cov = s["trim_coverage"]
    track = s["track_minimum_curvature_line"]
    bias = limits[f"{BRAKE_BIAS_NOMINAL:.2f}"]
    values = {
        "lltd_front_pct": 100.0 * s["vehicle"]["lltd_front"],
        "base_ackermann_pct": f"{signed(min(ack.values()))} to {signed(max(ack.values()))}",
        "steer_at_travel_deg": lock["mean_steer_at_travel_deg"],
        "steer_needed_3p5_deg": lock["mean_steer_needed_at_limit_deg"]["3.5m"],
        "steer_needed_3p5_powered_deg": 0.5 * (cell[(MU_SCALE, 3.5, 0.0, DESIGN)]["inner_deg"] + cell[(MU_SCALE, 3.5, 0.0, DESIGN)]["outer_deg"]),
        "cap_check_max_ms": s["cap_check"]["max_change_ms"],
        "tightest_radius_m": lock["tightest_cg_radius_at_limit_m"],
        "rack_needed_mm": lock["rack_needed_for_3p5m_at_limit_mm"],
        "arm_offset_mm": front["arm_offset_mm"],
        "fix_rack_mm": fix[0]["target_rack_mm"],
        "mu_nominal": mus[0], "mu_sensitivity": mus[-1],
        "best_lap_law": best_lap[mus[0]], "best_lap_law_sensitivity": best_lap[mus[-1]],
        "best_lap_laws": ", ".join(tied[mus[0]]), "best_lap_laws_sensitivity": ", ".join(tied[mus[-1]]),
        "convergence_band_ms": 1000.0 * band,
        "pro50_lap_range_s": span(min(sens[v]["+50%"]["delta_s"] for v in sens), max(sens[v]["+50%"]["delta_s"] for v in sens), " s", 2),
        "anti50_lap_range_s": span(min(sens[v]["-50%"]["delta_s"] for v in sens), max(sens[v]["-50%"]["delta_s"] for v in sens), " s", 2),
        "base_ackermann_full_travel_pct": ack[f"{RACK_TRAVEL_MM:g}mm"],
        "caps_count": sum(1 for c in s["operating_map"]["caps"] if not c.get("gap")),
        "gap_count": sum(1 for c in s["operating_map"]["caps"] if c.get("gap")),
        "lock_limited_count": len(s["operating_map"]["lock_limited_cells"]),
        "lock_limited_nominal_count": sum(1 for c in s["operating_map"]["lock_limited_cells"] if "variant" not in c),
        "lock_margin_min_deg": min(s["operating_map"]["lock_margin_min_deg"].values()),
        "best_lap_gain_s": -laps[mus[0]][best_lap[mus[0]]]["delta_s"],
        "best_lap_gain_sensitivity_s": -laps[mus[-1]][best_lap[mus[-1]]]["delta_s"],
        "anti50_lap_s": laps[mus[0]]["-50%"]["delta_s"], "anti50_lap_sensitivity_s": laps[mus[-1]]["-50%"]["delta_s"],
        "pro50_lap_s": laps[mus[0]]["+50%"]["delta_s"], "pro50_lap_sensitivity_s": laps[mus[-1]]["+50%"]["delta_s"],
        "design_lap_s": laps[mus[0]][DESIGN]["delta_s"], "design_lap_sensitivity_s": laps[mus[-1]][DESIGN]["delta_s"],
        "map_cells": len(best), "no_grip_cells": len(best) - len(signs), "pro_cells": sum(1 for v in signs if v > 0), "anti_cells": sum(1 for v in signs if v < 0),
        "parallel_cells": sum(1 for v in signs if v == 0),
        "best_3p5_steady": pct_label(best[(3.5, 0.0)][0]), "best_3p5_steady_gain_pct": best[(3.5, 0.0)][1],
        "best_3p5_hard_brake": next(pct_label(best[(3.5, a)][0]) for a in sorted(MAP_AX_G) if best[(3.5, a)][0] is not None),
        "pro50_apex_3p5": cell[(MU_SCALE, 3.5, 0.0, "+50%")]["ay_change_pct"],
        "anti50_apex_3p5": cell[(MU_SCALE, 3.5, 0.0, "-50%")]["ay_change_pct"],
        "pro50_authority_ratio_3p5": cell[(MU_SCALE, 3.5, 0.0, "+50%")]["n_delta_per_deg"] / cell[(MU_SCALE, 3.5, 0.0, BASELINE)]["n_delta_per_deg"],
        "circle_check_error_pct": s["circle_check"]["error_pct"],
        "coverage_pct": 100.0 * cov[BASELINE]["by_demand"]["0.98"]["all"], "coverage_exit_pct": 100.0 * cov[BASELINE]["by_demand"]["0.98"]["exit"],
        "coverage_braking_pct": 100.0 * cov[BASELINE]["by_demand"]["0.98"]["braking"], "coverage_steady_pct": 100.0 * cov[BASELINE]["by_demand"]["0.98"]["steady"],
        "coverage_100_pct": 100.0 * cov[BASELINE]["by_demand"]["1"]["all"], "coverage_95_pct": 100.0 * cov[BASELINE]["by_demand"]["0.95"]["all"],
        "coverage_anti50_pct": 100.0 * cov["-50%"]["by_demand"]["0.98"]["all"], "coverage_pro50_pct": 100.0 * cov["+50%"]["by_demand"]["0.98"]["all"],
        "coverage_demand_pct": 98.0,
        "clip_cells": s["clipped_braking"]["cells"], "clip_of": s["clipped_braking"]["of"],
        "clip_max_g": s["clipped_braking"]["max_g"], "clip_max_curve": s["clipped_braking"]["max_at"]["curve"],
        "clip_anti50_g": s["clipped_braking"]["by_curve"]["-50%"], "clip_pro50_g": s["clipped_braking"]["by_curve"]["+50%"],
        "ideal_bias_pct": 100.0 * bias["ideal_front_bias"],
        "ideal_limit_g": bias["ideal_limit_g"],
        "brake_model_gap_pts": s["brake_model_gap_pts"],
        "points_per_deg_toe_3p5": s["first_principles_optimum"]["3.5m"]["points_per_deg_toe_out"],
        "mass_gap_pts": s["mass_gap_pts"],
        "track_length_m": track["length_m"],
        "corners_under_15m": track["corners_under_15m"],
        "tightest_corner_m": min(c["min_radius_m"] for c in track["corners"]),
        "tire_sha256": s["tire"]["sha256"][:16],
    }
    (out / "readme.json").write_text(json.dumps(finite(values), indent=2), encoding="utf-8")


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)


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


def plot_grip(path, rows, base_pct, op_rows):
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.5, 4.4), facecolor=SURFACE)
    pct = np.array(ACKERMANN_PCT, dtype=float)
    plot_best(right, op_rows)
    for radius, color in zip(RADII_M, RADIUS_COLORS):
        def series(key, select):
            picked = [next(r.get(key) for r in rows if select(r) and r["radius_m"] == radius and r["curve"] == f"{p:+d}%") for p in ACKERMANN_PCT]
            return np.array([np.nan if v is None else v for v in picked], dtype=float)

        nominal = series("ay_change_pct", is_nominal)
        cases = {(r["tire_case"], r["slip_side"], r["lltd_offset"]) for r in rows}
        spread = np.array([series("ay_change_pct", lambda r, c=c: (r["tire_case"], r["slip_side"], r["lltd_offset"]) == c) for c in cases])
        left.fill_between(pct, np.nanmin(spread, axis=0), np.nanmax(spread, axis=0), color=color, alpha=0.15, lw=0)
        left.plot(pct, nominal, color=color, lw=2, marker="o", ms=4, label=f"R = {radius:g} m")
        design_gain = next(r["ay_change_pct"] for r in rows if is_nominal(r) and r["radius_m"] == radius and r["curve"] == DESIGN)
        left.plot([base_pct[radius]], [design_gain], marker="D", ms=7, color=color, mec=SURFACE, mew=1.5)
    left.axhline(0.0, color=MUTED, lw=0.8)
    left.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    left.set_ylabel("Max lateral g change vs parallel steer (%)", color=INK)
    left.set_title(f"Apex grip, coasting (band = 12-case range, diamond = {DESIGN})", color=INK, loc="left", fontsize=10)
    style(left)
    left.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_best(ax, op_rows):
    best = best_laws(op_rows, MU_SCALE)
    values = np.array([[np.nan if best[(r, a)][0] is None else best[(r, a)][0] for r in MAP_RADII_M] for a in MAP_AX_G], dtype=float)
    ax.imshow(values, cmap=GAIN_MAP, norm=TwoSlopeNorm(0.0, min(MAP_PCTS), max(MAP_PCTS)), aspect="auto", origin="lower")
    for i, a in enumerate(MAP_AX_G):
        for j, r in enumerate(MAP_RADII_M):
            pct, gain = best[(r, a)]
            ax.text(j, i, best_text(pct, gain, "\n"), ha="center", va="center", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(MAP_RADII_M)), [f"{r:g}" for r in MAP_RADII_M])
    ax.set_yticks(range(len(MAP_AX_G)), [ax_label(a) for a in MAP_AX_G])
    ax.set_xlabel("Corner radius (m)", color=INK)
    ax.set_title("Best law vs parallel, powered (red anti, blue pro)", color=INK, loc="left", fontsize=10)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ax.spines.values():
        side.set_visible(False)


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
    global MU_SCALE, LKY_CASES, ACKERMANN_PCT, STEER_FIX_TARGETS_PCT, BRAKE_BIAS_NOMINAL
    global LLTD_OFFSETS, SLIP_SIDES, BRAKE_G, BRAKE_RADII_M, TOE_OUT_DEG, TOE_ACKERMANN_PCT, MASS_CASES
    global MAP_RADII_M, MAP_AX_G, DERIV_RADII_M
    parser = argparse.ArgumentParser()
    parser.add_argument("--mu", type=float, default=MU_SCALE, help="tire grip scale, LMUY = LMUX")
    parser.add_argument("--ackermann", type=int, nargs="*", default=[], help="extra Ackermann %% to sweep and solve a linkage for")
    parser.add_argument("--bias", type=float, default=BRAKE_BIAS_NOMINAL, help="nominal front brake bias, 0 to 1")
    parser.add_argument("--quick", action="store_true", help="small sweep that runs every code path, for a smoke test")
    args = parser.parse_args(argv)
    MU_SCALE, BRAKE_BIAS_NOMINAL = args.mu, args.bias
    LKY_CASES = {**LKY_CASES, "lky_scaled": MU_SCALE}
    ACKERMANN_PCT = tuple(sorted(set(ACKERMANN_PCT) | set(args.ackermann)))
    STEER_FIX_TARGETS_PCT = tuple(sorted(set(STEER_FIX_TARGETS_PCT) | {float(p) for p in args.ackermann}))
    if args.quick:
        LLTD_OFFSETS, SLIP_SIDES, BRAKE_G, BRAKE_RADII_M = (0.0,), {"pos": 1.0}, (0.5,), (3.5,)
        TOE_OUT_DEG, TOE_ACKERMANN_PCT, MASS_CASES = (0.0,), (0, 50, 100), MASS_CASES[:1]
        MAP_RADII_M, MAP_AX_G, DERIV_RADII_M = (3.5, 4.5, 8.0, 15.0), (-0.6, 0.0, 0.15), (3.5,)


def main():
    apply_args()
    out = Path(os.environ["OUT_DIR"])
    orion = load_yaml(Path(os.environ["BOBSIM_VEHICLE"]))
    base = with_front(orion)
    (out / "vehicle_front.yml").write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")

    projection = project_vehicle_yaml(base)
    ggv = types.SimpleNamespace(**{f: getattr(projection.ggv, f) for f in GGV_FIELDS})
    for field, value in TEAM_2027.items():
        setattr(ggv, field, value)
    orion_ggv = project_vehicle_yaml(orion).ggv
    orion_table, _ = steer_table(orion)
    base_table, _ = steer_table(base)

    write_csv(out / "steering_curves.csv", [
        {"car": name, "rack_mm": rack, "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
         "ackermann_pct": ackermann_pct(inner, outer, g.track_front, g.wheelbase) if rack >= 2.0 else float("nan")}
        for name, t, g in (("Orion", orion_table, orion_ggv), (DESIGN, base_table, ggv)) for rack, inner, outer in t
    ])

    curves = {BASELINE: ConstantAckermann(0, ggv.track_front, ggv.wheelbase)}
    curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in ACKERMANN_PCT})
    curves[DESIGN] = TableCurve(base_table)
    outer_max = float(base_table[-1, 2])
    tir_path = tire_templates_root(base) / f"{base['front']['tire']['template']}.tir"
    tir = parse_tir(tir_path)
    ggv.lltd = balanced_lltd(ggv, tir, curves[BASELINE], outer_max)
    nominal_tire = {**tir, "LMUY": MU_SCALE, "LMUX": MU_SCALE, "LKY": LKY_CASES[NOMINAL[0]]}
    nominal_car = Car(ggv, (nominal_tire,) * 4, ggv.lltd)
    op_cars = {MU_SCALE: nominal_car}
    if MU_SENSITIVITY != MU_SCALE:
        lltd_sens = balanced_lltd(ggv, tir, curves[BASELINE], outer_max, MU_SENSITIVITY)
        sens_tire = {**tir, "LMUY": MU_SENSITIVITY, "LMUX": MU_SENSITIVITY, "LKY": LKY_CASES[NOMINAL[0]]}
        op_cars[MU_SENSITIVITY] = Car(ggv, (sens_tire,) * 4, lltd_sens)
    steer_targets = {
        pct: needed_mean_steer_deg(nominal_car, ConstantAckermann(pct, ggv.track_front, ggv.wheelbase), RADII_M[0], outer_max)
        for pct in STEER_FIX_TARGETS_PCT
    }
    biases = (BRAKE_BIAS_NOMINAL, float(base["brake"]["front_bias"]))
    cases = build_cases(ggv, tir, curves, outer_max, biases, orion, steer_targets, nominal_car)
    order = sorted(range(len(cases)), key=lambda i: (cost(cases[i]), i))
    solved = map_cases(solve_case, [cases[i] for i in order])
    results = [None] * len(cases)
    for i, result in zip(order, solved):
        results[i] = result
    by_kind = {}
    for case, result in zip(cases, results):
        by_kind.setdefault(case["kind"], []).append(result)
    rows, braking, toe, mass = ([row for result in by_kind[kind] for row in result] for kind in ("main", "braking", "toe", "mass"))
    line, fix = by_kind["track"][0], by_kind["fix"]
    corners = track_corners(line)
    braking_limit = {f"{b:.2f}": braking_limit_g(nominal_car, b) for b in biases}

    op_curves = {name: curves[name] for name in (BASELINE, *(f"{p:+d}%" for p in MAP_PCTS), DESIGN)}
    drive_diff = PLANNED_DIFF + (base["powertrain"]["pDriveline"]["diff_kineticFrictionRatio"],)
    rear_radius = float(base["rear"]["wheel"]["radius_m"])
    sens_cars = {
        "LLTD -0.10": (Car(ggv, (nominal_tire,) * 4, ggv.lltd - 0.10), BRAKE_BIAS_NOMINAL),
        "LLTD +0.10": (Car(ggv, (nominal_tire,) * 4, ggv.lltd + 0.10), BRAKE_BIAS_NOMINAL),
        f"bias {biases[1]:.2f}": (nominal_car, biases[1]),
    }
    sens_curves = {name: curves[name] for name in (BASELINE, *SENSITIVITY_CURVES)}

    def map_case(car, bias, radius, ax, labels, probe=False, deriv=False):
        if ax < 0.0:
            lon = Longitudinal(-ax, bias=bias)
        else:
            lon = Longitudinal(-ax, diff=drive_diff, wheel_radius=rear_radius)
        return {"kind": "map", "car": car, "curves": sens_curves if "variant" in labels else op_curves, "outer_max": outer_max,
                "radius": radius, "lon": lon, "probe": probe, "deriv": deriv, "labels": {**labels, "radius_m": radius, "ax_g": ax}}

    op_cases = [
        map_case(car, BRAKE_BIAS_NOMINAL, radius, ax, {"mu": mu}, mu == MU_SCALE, mu == MU_SCALE and ax == 0.0 and radius in DERIV_RADII_M)
        for mu, car in op_cars.items() for radius in MAP_RADII_M for ax in MAP_AX_G
    ]
    sens_cases = [
        map_case(car, bias, radius, ax, {"mu": MU_SCALE, "variant": variant})
        for variant, (car, bias) in sens_cars.items() for radius in MAP_RADII_M for ax in MAP_AX_G
    ]
    map_results = map_cases(solve_case, op_cases + sens_cases)
    op_rows = [row for result in map_results[:len(op_cases)] for row in result]
    sens_rows = [row for result in map_results[len(op_cases):] for row in result]
    for r in op_rows + sens_rows:
        r["lock_limited"] = bool(math.isfinite(r["ay_max_g"]) and r["lock_margin_deg"] < LOCK_MARGIN_MIN_DEG)
    power_w = float(load_yaml(repo_root() / LAP_CONFIG)["event"]["drive_power_limit_w"])

    def lap_limits(car, bias):
        return {"brake_g": braking_limit_g(car, bias)["limit_g"], "drive_g": exit_accel_g(car, ggv.max_drive_force),
                "mass": car.mass, "power_w": power_w}

    qss_cases, caps = [], []
    for mu, car in op_cars.items():
        limits = lap_limits(car, BRAKE_BIAS_NOMINAL)
        rows_mu = [r for r in op_rows if r["mu"] == mu]
        for name in op_curves:
            caps += [{"mu": mu, **c} for c in check_map(rows_mu, name, limits["brake_g"], limits["drive_g"])]
            qss_cases.append({"kind": "qss", "line": line["line"], "rows": rows_mu, **limits, "labels": {"mu": mu, "curve": name}})
    sens_qss = []
    for variant, (car, bias) in sens_cars.items():
        limits = lap_limits(car, bias)
        rows_v = [r for r in sens_rows if r["variant"] == variant]
        for name in sens_curves:
            caps += [{"variant": variant, **c} for c in check_map(rows_v, name, limits["brake_g"], limits["drive_g"])]
            sens_qss.append({"kind": "qss", "line": line["line"], "rows": rows_v, **limits, "labels": {"mu": MU_SCALE, "variant": variant, "curve": name}})
    refined = [{**c, "refine": True} for c in qss_cases if c["labels"]["mu"] == MU_SCALE and c["labels"]["curve"] in (BASELINE, *CONVERGENCE_CURVES)]
    fill_qss = cap_fill_cases(qss_cases, op_rows, caps)
    qss_results = map_cases(solve_case, qss_cases + refined + sens_qss + fill_qss)
    nominal_laps = qss_results[:len(qss_cases)]
    refined_laps = qss_results[len(qss_cases):len(qss_cases) + len(refined)]
    sens_laps = qss_results[len(qss_cases) + len(refined):len(qss_cases) + len(refined) + len(sens_qss)]
    fill_laps = qss_results[len(qss_cases) + len(refined) + len(sens_qss):]
    laps = lap_phases(nominal_laps, line["curvature"], op_rows)
    lap_time = {(r["curve"], r["refine"]): r["lap_time_s"] for r in nominal_laps + refined_laps if r["mu"] == MU_SCALE}
    convergence = {}
    for name in CONVERGENCE_CURVES:
        coarse = lap_time[(name, False)] - lap_time[(BASELINE, False)]
        fine = lap_time[(name, True)] - lap_time[(BASELINE, True)]
        convergence[name] = {"delta_s": round(coarse, 4), "delta_refined_s": round(fine, 4), "change_s": round(fine - coarse, 4)}
    convergence["band_s"] = round(2.0 * max(abs(v["change_s"]) for v in convergence.values()), 4)
    sensitivity = {}
    for variant, (car, bias) in sens_cars.items():
        base_lap = next(r["lap_time_s"] for r in sens_laps if r["variant"] == variant and r["curve"] == BASELINE)
        cell = {(r["radius_m"], r["ax_g"], r["curve"]): r["ay_change_pct"] for r in sens_rows if r["variant"] == variant}
        sensitivity[variant] = {
            name: {
                "delta_s": round(next(r["lap_time_s"] for r in sens_laps if r["variant"] == variant and r["curve"] == name) - base_lap, 3),
                "steady_3p5_pct": round(cell[(3.5, 0.0, name)], 2), "brake_3p5_pct": round(cell[(3.5, min(MAP_AX_G), name)], 2),
            } for name in SENSITIVITY_CURVES
        }
    coverage = {}
    for name in COVERAGE_CURVES:
        lap = next(r for r in nominal_laps if r["mu"] == MU_SCALE and r["curve"] == name)
        coverage[name] = trim_coverage(lap, line["curvature"], nominal_car, op_curves[name], outer_max, drive_diff, rear_radius, BRAKE_BIAS_NOMINAL)
    nominal_limits = next(c for c in qss_cases if c["labels"] == {"mu": MU_SCALE, "curve": BASELINE})
    circle = circle_check(nominal_limits["rows"], *(nominal_limits[k] for k in ("brake_g", "drive_g", "mass", "power_w")))
    for f in fix:
        f.pop("table")
    for name, data in (("limits", rows), ("braking", braking), ("toe", toe), ("mass_cg", mass), ("operating_map", op_rows),
                       ("operating_map_sensitivity", sens_rows)):
        write_csv(out / f"{name}.csv", data)

    nominal_base = {r["radius_m"]: r for r in rows if r["curve"] == DESIGN and is_nominal(r)}
    base_pct = {radius: r["ackermann_pct_at_limit"] for radius, r in nominal_base.items()}
    steer_band = (
        min(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_base.values() if r["radius_m"] <= 8.0),
        max(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_base.values() if r["radius_m"] <= 8.0),
    )
    tables = {"Orion": orion_table, DESIGN: base_table}
    geometry = {"Orion": (orion_ggv.track_front, orion_ggv.wheelbase), DESIGN: (ggv.track_front, ggv.wheelbase)}
    plot_curves(out / "ackermann_curves.png", tables, geometry, steer_band)
    plot_grip(out / "grip_vs_ackermann.png", rows, base_pct, op_rows)

    table_rows = {}
    for radius in RADII_M:
        table_rows[f"{radius:g}m"] = {
            r["curve"]: {"ay_change_pct": round(r["ay_change_pct"], 2), "limiting_axle": r.get("limiting_axle")}
            for r in rows if is_nominal(r) and r["radius_m"] == radius
        }
    spread = {}
    for radius in RADII_M:
        for p in ACKERMANN_PCT:
            values = [r["ay_change_pct"] for r in rows if r["radius_m"] == radius and r["curve"] == f"{p:+d}%"]
            spread.setdefault(f"{radius:g}m", {})[f"{p:+d}%"] = [round(float(np.nanmin(values)), 2), round(float(np.nanmax(values)), 2)]
    slip_norm = [r for r in braking if r["combined_slip"] == "slip_norm"]
    ellipse = [r for r in braking if r["combined_slip"] == "ellipse"]

    summary = {
        "vehicle": {
            "note": f"{DESIGN} front hardpoints, team 2027 mass and CG, balanced LLTD, Orion rear and tire",
            "mass_kg": round(ggv.mass, 2), "cg_height_m": round(ggv.cg_height, 4),
            "front_static_frac": round(ggv.front_static_frac, 4), "wheelbase_m": round(ggv.wheelbase, 4),
            "track_front_m": round(ggv.track_front, 4), "track_rear_m": round(ggv.track_rear, 4),
            "lltd_front": round(ggv.lltd, 4), "lltd_source": f"balanced at R = {BALANCE_RADIUS_M:g} m",
        },
        "tire": {"template": base["front"]["tire"]["template"], "sha256": hashlib.sha256(tir_path.read_bytes()).hexdigest(),
                 "LMUY_LMUX": MU_SCALE, "LKY_cases": LKY_CASES},
        "ackermann_pct_at_rack": {
            name: {f"{r:g}mm": round(float(ackermann_pct(at_rack(t, r, 1), at_rack(t, r, 2), *geometry[name])), 1) for r in REPORT_RACK_MM}
            for name, t in tables.items()
        },
        "nominal": table_rows,
        "ay_change_pct_range_all_cases": spread,
        "brake_model_gap_pts": max_change_gap(slip_norm, ellipse, ("brake_bias_front", "brake_g", "radius_m", "curve"), BRAKE_CURVES),
        "mass_gap_pts": max_change_gap(mass, [r for r in rows if is_nominal(r)], ("radius_m", "curve"), MASS_CURVES),
        "toe": toe_summary(toe),
        "team_2027_inputs": {
            "mass_kg": round(TEAM_2027["mass"], 1), "cg_height_m": round(TEAM_2027["cg_height"], 4),
            "front_static_frac": TEAM_2027["front_static_frac"], "straight_braking_limit": braking_limit,
        },
        "lock_at_rack_travel": lock_check(base_table, rows),
        "front_base_linkage": {"arm_offset_mm": arm_offset_mm(base), "inner_wheel_at_travel": lock_geometry(base, RACK_TRAVEL_MM)},
        "steering_fix": fix,
        "first_principles_optimum": optimum_check(nominal_car, rows),
        "track_minimum_curvature_line": {
            "track": line["track"], "length_m": round(line["length_m"], 1), "corners_under_15m": len(corners),
            "corners": [{"turn_deg": round(math.degrees(t), 1), "min_radius_m": round(r, 2)} for t, r in corners],
        },
        "operating_map": {
            "radii_m": MAP_RADII_M, "ax_g": MAP_AX_G, "curves": list(op_curves), "mu": list(op_cars),
            "lltd_front": {f"{mu:g}": round(car.lltd, 4) for mu, car in op_cars.items()},
            "law_no_grip_cells": sum(1 for r in op_rows if not math.isfinite(r["ay_max_g"])),
            "caps": caps,
            "lock_limited_cells": [{k: r[k] for k in ("mu", "radius_m", "ax_g", "curve", "ay_max_g") if k in r} | ({"variant": r["variant"]} if "variant" in r else {})
                                   for r in op_rows + sens_rows if r["lock_limited"]],
            "lock_margin_min_deg": {name: round(min(r["lock_margin_deg"] for r in op_rows if r["curve"] == name and math.isfinite(r["ay_max_g"])), 2)
                                    for name in op_curves},
        },
        "sensitivity": sensitivity,
        "lap_qss": laps,
        "circle_check": circle,
        "grid_convergence": convergence,
        "cap_check": cap_check_result(nominal_laps, fill_laps),
        "trim_coverage": coverage,
        "clipped_braking": clipped_braking(op_rows),
    }
    (out / "summary.json").write_text(json.dumps(finite(summary), indent=2, allow_nan=False), encoding="utf-8")
    write_readme_parts(out, json.loads(json.dumps(finite(summary))), op_rows)


if __name__ == "__main__":
    main()
