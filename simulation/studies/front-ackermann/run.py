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

from app.tire_eval import _mf52_fx_pure, _mf52_fy_pure
from common.vehicle_io import load_yaml, parse_tir, repo_root, tire_templates_root
from engines.kinpy.kinematics import CornerKinematics
from simulations.envelope.vehicle_yaml import project_vehicle_yaml
from simulations.lap.core.qss import GGVMap, solve_qss_lap
from simulations.lap.core.racing_line import optimize_racing_line
from simulations.lap.core.track import RacingLine, TrackCorridor
from tools.parallel import map_cases

G = 9.80665
LB_TO_KG = 0.45359237
IN_TO_M = 0.0254
HERE = Path(__file__).resolve().parent
NOMINAL = ("lky_1", "pos", 0.0)
BASELINE = "+0%"
REAR_BASELINE = "open"
GGV_FIELDS = ("mass", "wheelbase", "cg_height", "track_front", "track_rear", "front_static_frac", "lltd", "max_drive_force")

RADIUS_COLORS = ("#0d366b", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef")
GAIN_MAP = LinearSegmentedColormap.from_list("gain", ("#e34948", "#f0efec", "#2a78d6"))
DESIGN_COLOR, ORION_COLOR = "#2a78d6", "#eb6834"
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


class Inputs(types.SimpleNamespace):
    @property
    def lky_cases(self):
        return {"lky_1": 1.0, "lky_scaled": self.mu}

    def tire(self, tir, lky_name):
        return {**tir, "LMUY": self.mu, "LMUX": self.mu, "LKY": self.lky_cases[lky_name]}


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def value(x):
    if isinstance(x, dict):
        return value(x["value"]) if "value" in x else {k: value(v) for k, v in x.items()}
    if isinstance(x, list):
        return tuple(value(v) for v in x)
    return x


def grid(spec):
    return np.arange(spec["start"], spec["stop"], spec["step"])


def bundle_id(vehicle_path):
    path = vehicle_path.with_name("bundle.yml")
    return load(path).get("bundle_id") if path.is_file() else None


def load_inputs(path, vehicle, quick=False):
    study = load(path)
    front, car, lsd, method = study["front_geometry"], value(study["car"]), value(study["lsd"]), value(study["method"])
    sweep = value(study["sweep"])
    quick_sweep = value(study["quick"]) if quick else {}
    if set(quick_sweep) - set(sweep):
        raise SystemExit(f"{path}: quick names inputs that the sweep does not have: {sorted(set(quick_sweep) - set(sweep))}")
    wheels = [vehicle[axle]["wheel"] for axle in ("front", "rear")]
    camber, toe = {float(w["camber_deg"]) for w in wheels}, {float(w["toe_deg"]) for w in wheels}
    if len(camber) != 1 or toe != {0.0}:
        raise SystemExit("The trim model takes one static camber on all four wheels and no static toe.")
    speeds = method.pop("qss_speeds_mps")
    method["rack_mm"], method["fx_max_fz_n"] = grid(method.pop("rack_grid_mm")), grid(method.pop("fx_max_fz_grid_n"))
    return Inputs(
        **{**sweep, **quick_sweep}, **value(study["policy"]), **value(study["rules"]), **method,
        question=study["question"], bundle=study["bundle"], lap_config=study["lap_config"], design=front["name"],
        front_m={k: [c / 1000.0 for c in v] for k, v in value(front["hardpoints_mm"]).items()},
        rack_pickup_m=[c / 1000.0 for c in value(front["rack_pickup_mm"])],
        rack_travel_mm=value(front["rack_travel_mm"]),
        team_2027={
            "mass": (car["car_mass_lb"] + car["driver_mass_lb"]) * LB_TO_KG,
            "cg_height": car["cg_height_in"] * IN_TO_M, "front_static_frac": car["front_static_frac"],
        },
        team_camber_deg=car["camber_case_deg"], static_camber_deg=camber.pop(), static_toe_deg=toe.pop(),
        mu=value(study["tire"]["grip_scale"]), mu_sensitivity=value(study["tire"]["grip_scale_sensitivity"]),
        brake_bias=value(study["brakes"]["front_bias_nominal"]),
        planned_diff=(lsd["preload_nm"], lsd["lock"]),
        qss_speeds_mps=tuple(grid(speeds)) + speeds["then"],
    )


class Car:
    def __init__(self, ggv, tires, lltd, inputs, camber_deg=None):
        self.mass, self.wheelbase, self.cg_height = ggv.mass, ggv.wheelbase, ggv.cg_height
        self.track_front, self.track_rear = ggv.track_front, ggv.track_rear
        self.front_frac, self.lltd, self.tires = ggv.front_static_frac, lltd, tires
        self.fz_grid = inputs.fx_max_fz_n
        a = self.wheelbase * (1.0 - self.front_frac)
        b = self.wheelbase * self.front_frac
        self.corners = (
            (a, self.track_front / 2), (a, -self.track_front / 2),
            (-b, self.track_rear / 2), (-b, -self.track_rear / 2),
        )
        self.combined = "ellipse"
        c = math.radians(inputs.static_camber_deg if camber_deg is None else camber_deg)
        self.camber = (c, -c, c, -c)
        self.fx_max_n, self.kappa_peak, self.alpha_peak = {-1: [], 1: []}, {-1: [], 1: []}, []
        for t in (tires[0], tires[2]):
            for sign, bounds in ((-1, (-0.5, 0.0)), (1, (0.0, 0.5))):
                fx = [minimize_scalar(lambda k: -abs(_mf52_fx_pure(t, fz, k, 0.0)), bounds=bounds, method="bounded") for fz in self.fz_grid]
                self.fx_max_n[sign].append(np.array([abs(r.fun) for r in fx]))
                self.kappa_peak[sign].append(np.array([abs(r.x) for r in fx]))
            fy = [minimize_scalar(lambda a: -lateral_n(t, fz, a, 1.0), bounds=(0.0, 0.6), method="bounded") for fz in self.fz_grid]
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
        return float(np.interp(fz, self.fz_grid, table[corner // 2]))

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


def with_front(vehicle, inputs, tie_o_dy_m=0.0, tie_o_dx_m=0.0, rack_dy_m=0.0, rack_dz_m=0.0):
    base = copy.deepcopy(vehicle)
    base["front"]["suspension"].update(copy.deepcopy(inputs.front_m))
    base["front"]["suspension"]["tie_o_m"][0] += tie_o_dx_m
    base["front"]["suspension"]["tie_o_m"][1] += tie_o_dy_m
    base["front"]["steering"]["rack_pickup_m"] = [
        inputs.rack_pickup_m[0], inputs.rack_pickup_m[1] + rack_dy_m, inputs.rack_pickup_m[2] + rack_dz_m,
    ]
    return base


def steer_table(vehicle, rack_mm):
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
    static = 2.0 * preload / radius + lock * total_n
    if static == 0.0:
        return (total_n / 2, total_n / 2)
    tire = car.tires[2]

    def wheel_fx(wheel_speed):
        return tuple(_mf52_fx_pure(tire, fz, (wheel_speed - v) / v, 0.0) for fz, v in zip(loads, speeds))

    grid = np.linspace(min(speeds), 1.25 * max(speeds), 9)
    gap = [sum(wheel_fx(w)) - total_n for w in grid]
    k = next((k for k in range(len(grid) - 1) if gap[k] <= 0.0 < gap[k + 1]), None)
    if k is not None:
        locked = wheel_fx(brentq(lambda w: sum(wheel_fx(w)) - total_n, grid[k], grid[k + 1], xtol=1e-6))
        if abs(locked[0] - locked[1]) <= static:
            return locked
    slip = math.copysign(kinetic * static / 2, speeds[1] - speeds[0])
    return (total_n / 2 + slip, total_n / 2 - slip)


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
    rear_speeds = tuple(speed * math.cos(beta) - yaw_rate * y for x, y in car.corners[2:])
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


def build_cases(inputs, ggv, tir, curves, outer_max, biases, orion, steer_targets, nominal_car):
    cases = []
    for lky_name, lky in inputs.lky_cases.items():
        tire = {**tir, "LMUY": inputs.mu, "LMUX": inputs.mu, "LKY": lky}
        for lltd_offset in inputs.lltd_offsets:
            car = Car(ggv, (tire,) * 4, ggv.lltd + lltd_offset, inputs)
            for side_name, side in inputs.slip_sides.items():
                for radius in inputs.radii_m:
                    cases.append({
                        "kind": "main", "car": car, "curves": curves, "outer_max": outer_max,
                        "radius": radius, "side": side, "grip_probe": inputs.grip_probe,
                        "probe": (lky_name, side_name, lltd_offset) == NOMINAL,
                        "labels": {
                            "tire_case": lky_name, "slip_side": side_name, "lltd_front": round(car.lltd, 4),
                            "lltd_offset": lltd_offset, "radius_m": radius,
                        },
                    })
    brake_curves = {name: curves[name] for name in (BASELINE, *inputs.brake_curves)}
    for model in inputs.brake_models:
        car = copy.copy(nominal_car)
        car.combined = model
        for bias in biases:
            for brake_g in inputs.brake_g:
                for radius in inputs.brake_radii_m:
                    cases.append({
                        "kind": "braking", "car": car, "curves": brake_curves, "outer_max": outer_max,
                        "radius": radius, "lon": Longitudinal(brake_g, bias=bias),
                        "labels": {"combined_slip": model, "brake_bias_front": bias, "brake_g": brake_g, "radius_m": radius},
                    })
    toe_curves = {BASELINE: curves[BASELINE]}
    toe_curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in inputs.toe_ackermann_pct})
    for toe in inputs.toe_out_deg:
        for radius in inputs.toe_radii_m:
            cases.append({
                "kind": "toe", "car": nominal_car, "curves": {k: ToeCurve(v, toe) for k, v in toe_curves.items()},
                "outer_max": outer_max, "radius": radius, "lon": None,
                "labels": {"toe_out_deg": toe, "radius_m": radius},
            })
    mass_curves = {name: curves[name] for name in (BASELINE, *inputs.mass_curves)}
    tires = nominal_car.tires
    for name, field, delta in inputs.mass_cases:
        variant = types.SimpleNamespace(**{f: getattr(ggv, f) for f in GGV_FIELDS})
        if field == "camber":
            car = Car(variant, tires, nominal_car.lltd, inputs, inputs.team_camber_deg)
        else:
            setattr(variant, field, getattr(variant, field) + delta)
            car = Car(variant, tires, nominal_car.lltd, inputs)
        for radius in inputs.toe_radii_m:
            cases.append({
                "kind": "mass", "car": car, "curves": mass_curves, "outer_max": outer_max, "radius": radius,
                "lon": None, "labels": {"variant": name, "radius_m": radius},
            })
    cases.append({"kind": "track", "lap_config": inputs.lap_config})
    for target, target_deg in steer_targets.items():
        cases.append({
            "kind": "fix", "vehicle": orion, "target_deg": target_deg, "target_pct": target,
            "track": ggv.track_front, "wheelbase": ggv.wheelbase, "inputs": inputs,
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
        return minimum_curvature_line(case["lap_config"])
    if case["kind"] == "map":
        return solve_map_case(case)
    if case["kind"] == "qss":
        return solve_qss_case(case)
    if case["kind"] == "coverage":
        return coverage_case(case)
    return steering_fix(case["vehicle"], case["target_deg"], case["target_pct"], case["track"], case["wheelbase"], case["inputs"])


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
            front_gain = probe_gain(car.with_grip(front=case["grip_probe"]), curve, radius, side, outer_max, ay)
            rear_gain = probe_gain(car.with_grip(rear=case["grip_probe"]), curve, radius, side, outer_max, ay)
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


def minimum_curvature_line(lap_config):
    config = load_yaml(repo_root() / lap_config)["track"]
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


def track_corners(line, radius_max_m):
    tight = np.abs(line["curvature"]) > 1.0 / radius_max_m
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


def balanced_lltd(ggv, tire, curve, outer_max, inputs):
    car = Car(ggv, (tire,) * 4, 0.5, inputs)

    def negative_ay(lltd):
        car.lltd = lltd
        limit = limit_ay(car, curve, inputs.balance_radius_m, 1.0, outer_max)
        return 0.0 if limit is None else -limit[1]

    return minimize_scalar(negative_ay, bounds=(0.2, 0.8), method="bounded", options={"xatol": 1e-3}).x


def needed_mean_steer_deg(car, curve, radius, outer_max):
    (beta, outer), ay = limit_ay(car, curve, radius, 1.0, outer_max)
    return math.degrees(0.5 * (curve(outer) + outer))


def lock_check(table, rows, car, curve, outer_max, tire_width_m, inputs):
    nominal = sorted((r for r in rows if is_nominal(r) and r["curve"] == inputs.design), key=lambda r: r["radius_m"])
    radii = [r["radius_m"] for r in nominal]
    need = [0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal]
    mean = np.degrees(0.5 * (table[:, 1] + table[:, 2]))
    reach = float(np.interp(inputs.rack_travel_mm, table[:, 0], mean))
    outer_full = float(np.interp(inputs.rack_travel_mm, table[:, 0], table[:, 2]))

    def outer_excess(radius):
        z = trim(car, curve, radius, 1.0, 0.02, outer_max)
        return 1.0 if z is None else z[1] - outer_full

    edge = inputs.hairpin_outside_diameter_m / 2 - car.track_front / 2 - tire_width_m / 2
    return {
        "rack_travel_mm": inputs.rack_travel_mm,
        "mean_steer_at_travel_deg": round(reach, 1),
        "min_cg_radius_low_speed_m": round(brentq(outer_excess, 2.5, 12.0, xtol=1e-3), 2),
        "rule_hairpin_cg_radius_m": round(math.sqrt(edge ** 2 - (car.wheelbase * (1.0 - car.front_frac)) ** 2), 2),
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


def lock_geometry(vehicle, rack_mm, bump_mm):
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
    for jounce in (-bump_mm, 0.0, bump_mm):
        xj, pj, rj = corner.solve_jounce(jounce / 1000.0, guess, rack_displacement_m=rack_mm / 1000.0)
        toe[jounce] = corner.curve_values(pj, xj, rj)["toe_deg"]
    return {
        "arm_to_tie_rod_angle_deg": round(angle, 1),
        "toggle_margin_deg": round(min(angle, 180.0 - angle), 1),
        "bump_toe_change_deg": {f"{j:+g}mm": round(toe[j] - toe[0.0], 3) for j in (-bump_mm, bump_mm)},
    }


def steering_fix(vehicle, target_deg, target_pct, track, wheelbase, inputs):
    fix_rack = inputs.steer_reserve * inputs.rack_travel_mm
    rack = np.append(np.arange(0.0, fix_rack, 0.5), fix_rack)

    def build(d):
        return with_front(vehicle, inputs, d[1] / 1000.0, d[0] / 1000.0, d[2] / 1000.0, d[3] / 1000.0)

    def errors(d):
        table, corner = steer_table(build(d), rack)
        if len(table) == 0 or table[-1, 0] < fix_rack:
            return None
        mean = math.degrees(0.5 * (table[-1, 1] + table[-1, 2]))
        pct = float(ackermann_pct(table[-1, 1], table[-1, 2], track, wheelbase))
        return [mean - target_deg, pct - target_pct, bump_toe_deg(corner, inputs.bump_mm), bump_toe_deg(corner, -inputs.bump_mm)]

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
    table, _ = steer_table(fixed, inputs.rack_mm)
    solved = bool(solved and len(table) > 0 and table[-1, 0] >= inputs.rack_travel_mm)
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
        result["inner_wheel_at_travel"] = lock_geometry(fixed, inputs.rack_travel_mm, inputs.bump_mm)
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
        limit = limit_ay(car, curve, radius, 1.0, outer_max, lon, tol=case["tol"])
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
                front = probe_gain(car.with_grip(front=case["grip_probe"]), curve, radius, 1.0, outer_max, limit[1], lon)
                rear = probe_gain(car.with_grip(rear=case["grip_probe"]), curve, radius, 1.0, outer_max, limit[1], lon)
                row["limiting_axle"] = limiting_axle(front, rear)
            if case["deriv"]:
                row.update(yaw_derivatives(car, curve, radius, outer_max, case["deriv_fraction"] * limit[1], lon))
        rows.append(row)
    return rows


def check_map(rows, curve, brake_g, drive_g, inputs):
    grid = {(r["radius_m"], r["ax_g"]): r["ay_max_g"] for r in rows if r["curve"] == curve}
    caps = []
    for radius in inputs.map_radii_m:
        if not math.isfinite(grid[(radius, 0.0)]):
            raise RuntimeError(f"{curve} has no steady limit at {radius} m")
        for sign, endpoint in ((-1.0, brake_g), (1.0, drive_g)):
            levels = sorted((x for x in inputs.map_ax_g if x * sign > 0), key=abs)
            finite = [math.isfinite(grid[(radius, x)]) for x in levels]
            if any(f and not all(finite[:k]) for k, f in enumerate(finite)):
                raise RuntimeError(f"{curve} has an envelope gap at {radius} m")
            if not all(finite):
                top = abs(levels[finite.index(False) - 1]) if finite.index(False) > 0 else 0.0
                caps.append({"curve": curve, "radius_m": radius, "side": "drive" if sign > 0 else "brake",
                             "cap_g": top, "endpoint_g": round(endpoint, 3)})
    return caps


def envelope_ggv(rows, curve, brake_g, drive_g, mass, power_w, inputs, refine=False):
    radii = np.asarray(inputs.map_radii_m)
    grid = {(r["radius_m"], r["ax_g"]): r["ay_max_g"] for r in rows if r["curve"] == curve}
    if not all(math.isfinite(grid[(r, 0.0)]) for r in inputs.map_radii_m):
        raise RuntimeError(f"{curve} has no steady limit at some radius")

    def at(radius, ax):
        return float(np.interp(radius, radii, [grid[(r, ax)] for r in inputs.map_radii_m]))

    def side(radius, steady, sign, endpoint):
        points, top = [(steady, 0.0)], endpoint
        for x in sorted((x for x in inputs.map_ax_g if x * sign > 0), key=abs):
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

    speeds = np.asarray(inputs.qss_speeds_mps, dtype=float)
    step = inputs.qss_ay_step_g
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
                       case["inputs"], case.get("refine", False))
    lap = solve_qss_lap(case["line"], ggv)
    if not lap.converged:
        raise RuntimeError(f"QSS lap did not converge for {case['labels']}")
    return {**case["labels"], "refine": case.get("refine", False), "lap_time_s": lap.lap_time_s, "segment_time_s": lap.segment_time_s,
            "ax_mps2": lap.longitudinal_acceleration_mps2, "speed_mps": lap.speed_mps}


def cap_fill_cases(qss_cases, op_rows, caps, inputs):
    if not any(c.get("mu") == inputs.mu for c in caps):
        return []
    top = {}
    for r in op_rows:
        if r["mu"] == inputs.mu and math.isfinite(r["ay_max_g"]):
            top[(r["radius_m"], r["ax_g"])] = max(top.get((r["radius_m"], r["ax_g"]), 0.0), r["ay_max_g"])
    rows = [r if math.isfinite(r["ay_max_g"]) else {**r, "ay_max_g": top.get((r["radius_m"], r["ax_g"]), float("nan"))}
            for r in op_rows if r["mu"] == inputs.mu]
    return [{**c, "rows": rows, "labels": {**c["labels"], "fill": True}} for c in qss_cases if c["labels"]["mu"] == inputs.mu]


def cap_check_result(nominal_laps, fill_laps, inputs):
    if not fill_laps:
        return {"laws": {}, "max_change_ms": 0.0}
    base = {k: next(r["lap_time_s"] for r in laps if r["mu"] == inputs.mu and r["curve"] == BASELINE)
            for k, laps in (("capped", nominal_laps), ("filled", fill_laps))}
    change = {}
    for r in fill_laps:
        if r["curve"] == BASELINE:
            continue
        capped = next(n["lap_time_s"] for n in nominal_laps if n["mu"] == inputs.mu and n["curve"] == r["curve"]) - base["capped"]
        change[r["curve"]] = round(1000.0 * (r["lap_time_s"] - base["filled"] - capped), 2)
    return {"laws": change, "max_change_ms": round(max(abs(v) for v in change.values()), 2)}


def circle_check(rows, brake_g, drive_g, mass, power_w, inputs):
    radius, count = inputs.circle_check_radius_m, 400
    angle = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    step = 2.0 * math.pi * radius / count
    line = RacingLine(
        station_m=np.arange(count) * step, x_m=radius * np.cos(angle), y_m=radius * np.sin(angle),
        heading_rad=angle + math.pi / 2.0, curvature_per_m=np.full(count, 1.0 / radius),
        segment_length_m=np.full(count, step), gate_offsets_m=np.zeros(count), track_length_m=count * step,
    )
    lap = solve_qss_lap(line, envelope_ggv(rows, BASELINE, brake_g, drive_g, mass, power_w, inputs))
    ay = next(r["ay_max_g"] for r in rows if r["curve"] == BASELINE and r["ax_g"] == 0.0 and r["radius_m"] == radius)
    expected = 2.0 * math.pi * radius / math.sqrt(ay * G * radius)
    error = lap.lap_time_s / expected - 1.0
    if abs(error) > inputs.circle_check_tol:
        raise RuntimeError(f"QSS circle check failed: {lap.lap_time_s:.4f} s against {expected:.4f} s")
    return {"radius_m": radius, "qss_s": round(lap.lap_time_s, 4), "hand_s": round(expected, 4),
            "error_pct": round(100.0 * error, 3)}


def condition_label(row):
    car = row.get("variant") or f"grip {row['mu']:g}"
    return f"{car}, {row['rear']}, {row['radius_m']:g} m, {ax_label(row['ax_g'])}"


def robust_region(rows, tols):
    cells = {}
    for r in rows:
        cells.setdefault(condition_label(r), {})[r["curve"]] = r["ay_max_g"]
    out = {}
    for law in dict.fromkeys(r["curve"] for r in rows):
        worst = (0.0, "")
        for label, cell in cells.items():
            best = max((v for v in cell.values() if math.isfinite(v)), default=float("nan"))
            if not math.isfinite(best):
                continue
            ay = cell.get(law, float("nan"))
            short = 100.0 * (1.0 - ay / best) if math.isfinite(ay) else 100.0
            worst = max(worst, (short, label))
        out[law] = {"worst_shortfall_pct": round(worst[0], 2), "binding": worst[1]}
    floor = min(v["worst_shortfall_pct"] for v in out.values())
    for v in out.values():
        v["in_region"] = {f"{tol:g}": v["worst_shortfall_pct"] <= floor + tol for tol in tols}
    return out


def hairpin_time_s(rows, curve, radius):
    ay = next(r["ay_max_g"] for r in rows if r["curve"] == curve and r["ax_g"] == 0.0 and r["radius_m"] == radius)
    return math.pi * radius / math.sqrt(ay * G * radius)


def best_laws(op_rows, inputs):
    cell = {(r["radius_m"], r["ax_g"], r["curve"]): r for r in op_rows if r["mu"] == inputs.mu}
    out = {}
    for radius in inputs.map_radii_m:
        for ax in inputs.map_ax_g:
            laws = [(cell[(radius, ax, f"{p:+d}%")], p) for p in inputs.map_pcts]
            if not math.isfinite(cell[(radius, ax, BASELINE)]["ay_max_g"]):
                feasible = [p for r, p in laws if math.isfinite(r["ay_max_g"])]
                out[(radius, ax)] = (min(feasible), math.inf) if feasible else (None, float("nan"))
                continue
            gain, pct = max((r["ay_change_pct"], p) for r, p in laws if math.isfinite(r["ay_change_pct"]))
            out[(radius, ax)] = (pct, gain) if gain > inputs.best_min_gain_pct else (0, 0.0)
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
        ax, ay = case["demand"] * ax_g, case["demand"] * ay_g
        if phase == "braking":
            lon = Longitudinal(-ax, bias=bias)
        else:
            lon = Longitudinal(-ax, diff=case["drive_diff"], wheel_radius=case["rear_radius"])
        z = trim(car, curve, radius, 1.0, ay, outer_max, lon=lon)
        if z is None:
            guess = trim(car, curve, radius, 1.0, 0.5 * ay, outer_max, lon=lon)
            z = trim(car, curve, radius, 1.0, ay, outer_max, guess, lon) if guess is not None else None
        out.append((phase, dt, z is not None))
    return out


def trim_coverage(lap, curvature, car, curve, outer_max, drive_diff, rear_radius, bias, inputs):
    points, count = [], len(curvature)
    for i in range(0, count, inputs.coverage_step):
        ax = lap["ax_mps2"][i] / G
        phase = "braking" if ax < -0.05 else ("exit" if ax > 0.05 else "steady")
        j = (i + 1) % count if phase == "braking" else i
        ay = lap["speed_mps"][j] ** 2 * abs(curvature[j]) / G
        radius = 1.0 / max(abs(curvature[j]), 1e-9)
        if ay < 0.05 or radius > inputs.corner_radius_max_m:
            continue
        points.append((radius, ax, ay, lap["segment_time_s"][i], phase))
    chunks = [points[k::24] for k in range(24)]
    cases = [{"kind": "coverage", "car": car, "curve": curve, "outer_max": outer_max, "drive_diff": drive_diff,
              "rear_radius": rear_radius, "bias": bias, "demand": inputs.coverage_demand, "points": chunk} for chunk in chunks if chunk]
    results = [r for chunk in map_cases(solve_case, cases) for r in chunk]
    out = {"points": len(points), "demand": inputs.coverage_demand, "radius_max_m": inputs.corner_radius_max_m}
    for phase in ("braking", "steady", "exit", "all"):
        sel = [(dt, ok) for p, dt, ok in results if phase in (p, "all")]
        out[phase] = round(sum(dt for dt, ok in sel if ok) / max(sum(dt for dt, _ in sel), 1e-9), 3)
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


def skidpad_time_s(rows, curve, inputs):
    steady = {r["radius_m"]: r["ay_max_g"] for r in rows if r["curve"] == curve and r["ax_g"] == 0.0}
    ay = float(np.interp(inputs.skidpad_radius_m, inputs.map_radii_m, [steady[r] for r in inputs.map_radii_m]))
    return 2.0 * math.pi * inputs.skidpad_radius_m / math.sqrt(ay * G * inputs.skidpad_radius_m)


def lap_phases(laps, curvature, op_rows, inputs):
    radius = 1.0 / np.maximum(np.abs(curvature), 1e-9)
    bands = np.digitize(radius, inputs.lap_radius_bands_m)
    band_names = [f"under {inputs.lap_radius_bands_m[0]:g} m", f"{inputs.lap_radius_bands_m[0]:g} to {inputs.lap_radius_bands_m[1]:g} m",
                  f"over {inputs.lap_radius_bands_m[1]:g} m"]
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
                "skidpad_delta_s": round(skidpad_time_s(rows_mu, lap["curve"], inputs) - skidpad_time_s(rows_mu, BASELINE, inputs), 4),
            }
        out[f"{mu:g}"][BASELINE] = {
            "lap_time_s": round(base["lap_time_s"], 3),
            "skidpad_time_s": round(skidpad_time_s([r for r in op_rows if r["mu"] == mu], BASELINE, inputs), 3),
            "time_share_by_band": {n: round(float(base["segment_time_s"][bands == i].sum() / base["lap_time_s"]), 3)
                                   for i, n in enumerate(band_names)},
        }
    out["radius_bands"] = band_names
    return out


def optimum_check(car, rows, inputs):
    out = {}
    for radius in inputs.radii_m[:4]:
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


def write_readme_parts(out, s, op_rows, inputs):
    nominal, spread, fix = s["nominal"], s["ay_change_pct_range_all_cases"], s["steering_fix"]
    laws = [f"{p:+d}%" for p in inputs.map_pcts]
    parts = {}
    parts["apex"] = table(["R", *laws], [
        [f"{r:g} m"] + [f"{signed(nominal[f'{r:g}m'][c]['ay_change_pct'])} ({span(*spread[f'{r:g}m'][c])})" for c in laws]
        for r in inputs.radii_m
    ])

    plane = 1000.0 * (inputs.front_m["wheel_center_m"][1] - inputs.front_m["tie_o_m"][1])
    front = s["front_base_linkage"]
    link = [linkage_row(inputs.design, None, front["arm_offset_mm"], plane, front["inner_wheel_at_travel"])]
    for f in fix:
        link.append(linkage_row(pct_label(f["target_ackermann_pct"]), f, f["arm_offset_mm"],
                                f["tie_o_inboard_of_wheel_center_plane_mm"], f.get("inner_wheel_at_travel")))
    parts["linkage"] = table(["Option", "Tie rod outer move", "To wheel center plane", "Arm to kingpin", "Rack pickup move",
                              "Toggle margin at inner lock", "Bump toe at inner lock, ±25 mm"], link)

    optimum = s["first_principles_optimum"]
    parts["optimum"] = table(["R", "Front-limited optimum", "Solver optimum (10% steps)", "+75% limited by"], [
        [f"{r:g} m", f"{optimum[f'{r:g}m']['yaw_weighted_optimum_pct']:.0f}%", s["toe"][f"toe-out 0 deg, {r:g} m"]["best_curve"],
         nominal[f"{r:g}m"]["+75%"]["limiting_axle"]] for r in inputs.toe_radii_m
    ])

    limits = s["team_2027_inputs"]["straight_braking_limit"]
    parts["bias"] = table(["Front bias", "Locks first", "Straight-line limit"], [
        [f"{100 * float(b):.0f}%", v["locks_first"], f"{v['limit_g']:.2f} g"] for b, v in limits.items()
    ])

    parts["toe"] = table(["Total toe-out", "3.5 m best", "4.5 m best"], [
        [signed(t, "°") + (" (toe-in)" if t < 0 and t == min(inputs.toe_out_deg) else "")]
        + [s["toe"][f"toe-out {t:g} deg, {r:g} m"]["best_curve"] for r in inputs.toe_radii_m] for t in inputs.toe_out_deg
    ])

    cell = {(r["mu"], r["radius_m"], r["ax_g"], r["curve"]): r for r in op_rows}
    best = best_laws(op_rows, inputs)

    def best_cell(radius, ax):
        pct, gain = best[(radius, ax)]
        axle = cell[(inputs.mu, radius, ax, BASELINE)].get("limiting_axle")
        return best_text(pct, gain) + (f" {axle[0].upper()}" if axle else "")

    parts["map"] = table(["Longitudinal", *(f"{r:g} m" for r in inputs.map_radii_m)], [
        [ax_label(a)] + [best_cell(r, a) for r in inputs.map_radii_m] for a in inputs.map_ax_g
    ])

    deriv = lambda r: f"{1000 * r['n_beta_per_deg']:+.1f} / {1000 * r['n_delta_per_deg']:.1f}".replace("-", "−")
    parts["limit"] = table(["R", *inputs.limit_curves], [
        [f"{radius:g} m"] + [deriv(cell[(inputs.mu, radius, 0.0, c)]) for c in inputs.limit_curves] for radius in inputs.deriv_radii_m
    ])

    others = [c for c in s["operating_map"]["curves"] if c != BASELINE]
    nominal_rows = [r for r in op_rows if r["mu"] == inputs.mu]
    parts["hairpin"] = table(["Option", *(f"{r:g} m" for r in inputs.toe_radii_m)], [
        [c] + [signed(1000 * (hairpin_time_s(nominal_rows, c, r) - hairpin_time_s(nominal_rows, BASELINE, r)), " ms", 0)
               for r in inputs.toe_radii_m] for c in others
    ])

    lock_rows = []
    for c in s["operating_map"]["curves"]:
        r = cell[(inputs.mu, 3.5, 0.0, c)]
        mean = 0.5 * (r["inner_deg"] + r["outer_deg"])
        lock_rows.append([c, f"{mean:.1f}°", f"{r['inner_deg']:.1f}° / {r['outer_deg']:.1f}°"])
    parts["lock"] = table(["Option", "Mean steer at the 3.5 m limit", "Inner / outer"], lock_rows)

    robust, beyond = s["robust"]["all"], s["robust"]["beyond_3p5"]
    yes = {True: "yes", False: "no"}
    parts["robust"] = table(["Option", "Worst shortfall", "Binding condition", "In region, 0.5 pt", "In region, 1 pt",
                             "Worst beyond 3.5 m", "In region beyond 3.5 m, 0.5 pt"], [
        [c, signed(robust[c]["worst_shortfall_pct"], "%", 1).lstrip("+"), robust[c]["binding"], yes[robust[c]["in_region"]["0.5"]],
         yes[robust[c]["in_region"]["1"]], signed(beyond[c]["worst_shortfall_pct"], "%", 1).lstrip("+"), yes[beyond[c]["in_region"]["0.5"]]]
        for c in sorted(robust, key=lambda c: robust[c]["worst_shortfall_pct"])
    ])

    laps = s["lap_qss"]
    mus = [m for m in laps if m != "radius_bands"]
    parts["lap"] = table(
        ["Option", *(f"Endurance lap, grip {m}" for m in mus), *(f"Skidpad lap, grip {m}" for m in mus)],
        [[c] + [signed(laps[m][c]["delta_s"], " s", 2) for m in mus]
         + [signed(laps[m][c]["skidpad_delta_s"], " s", 3) for m in mus] for c in others],
    )

    for name, text in parts.items():
        (out / f"{name}.md").write_text(text, encoding="utf-8")

    lock = s["lock_at_rack_travel"]
    ack = s["ackermann_pct_at_rack"][inputs.design]
    signs = [pct for pct, _ in best.values() if pct is not None]
    band = s["grid_convergence"]["band_s"]
    tied = {m: [c for c in others if abs(laps[m][c]["delta_s"]) <= band] for m in mus}
    region = {k: [c for c, v in s["robust"][k].items() if v["in_region"]["0.5"]] for k in s["robust"]}
    lsd = {(r["radius_m"], r["ax_g"], r["curve"]): r["ay_change_pct"] for r in s["lsd_rows"]}
    cov = s["trim_coverage"]
    track = s["track_minimum_curvature_line"]
    bias = limits[f"{inputs.brake_bias:.2f}"]
    values = {
        "question": inputs.question,
        "lltd_front_pct": 100.0 * s["vehicle"]["lltd_front"],
        "base_ackermann_pct": f"{signed(min(ack.values()))} to {signed(max(ack.values()))}",
        "steer_at_travel_deg": lock["mean_steer_at_travel_deg"],
        "steer_needed_3p5_deg": lock["mean_steer_needed_at_limit_deg"]["3.5m"],
        "steer_needed_3p5_powered_deg": 0.5 * (cell[(inputs.mu, 3.5, 0.0, inputs.design)]["inner_deg"] + cell[(inputs.mu, 3.5, 0.0, inputs.design)]["outer_deg"]),
        "cap_check_max_ms": s["cap_check"]["max_change_ms"],
        "tightest_radius_m": lock["tightest_cg_radius_at_limit_m"],
        "min_radius_low_speed_m": lock["min_cg_radius_low_speed_m"],
        "rule_hairpin_radius_m": lock["rule_hairpin_cg_radius_m"],
        "rear_baseline": REAR_BASELINE,
        "robust_floor_pct": min(v["worst_shortfall_pct"] for v in s["robust"]["all"].values()),
        "robust_laws": ", ".join(region["all"]), "robust_laws_beyond_3p5": ", ".join(region["beyond_3p5"]),
        "lsd_pro50_apex_3p5": lsd[(3.5, 0.0, "+50%")], "lsd_anti50_apex_3p5": lsd[(3.5, 0.0, "-50%")],
        "lsd_pro50_drive_3p5": lsd[(3.5, 0.3, "+50%")],
        "variants": ", ".join(s["operating_map"]["variants"]),
        "rack_needed_mm": lock["rack_needed_for_3p5m_at_limit_mm"],
        "arm_offset_mm": front["arm_offset_mm"],
        "fix_rack_mm": fix[0]["target_rack_mm"],
        "mu_nominal": mus[0], "mu_sensitivity": mus[-1],
        "lap_tied_laws": ", ".join(tied[mus[0]]), "lap_tied_laws_sensitivity": ", ".join(tied[mus[-1]]),
        "convergence_band_ms": 1000.0 * band,
        "base_ackermann_full_travel_pct": ack[f"{inputs.rack_travel_mm:g}mm"],
        "caps_count": len(s["operating_map"]["caps"]),
        "lock_limited_count": len(s["operating_map"]["lock_limited_cells"]),
        "lock_limited_nominal_count": sum(1 for c in s["operating_map"]["lock_limited_cells"] if "variant" not in c),
        "lock_margin_min_deg": min(s["operating_map"]["lock_margin_min_deg"].values()),
        "anti50_lap_s": laps[mus[0]]["-50%"]["delta_s"], "anti50_lap_sensitivity_s": laps[mus[-1]]["-50%"]["delta_s"],
        "pro50_lap_s": laps[mus[0]]["+50%"]["delta_s"], "pro50_lap_sensitivity_s": laps[mus[-1]]["+50%"]["delta_s"],
        "design_lap_s": laps[mus[0]][inputs.design]["delta_s"], "design_lap_sensitivity_s": laps[mus[-1]][inputs.design]["delta_s"],
        "map_cells": len(best), "no_grip_cells": len(best) - len(signs), "pro_cells": sum(1 for v in signs if v > 0), "anti_cells": sum(1 for v in signs if v < 0),
        "parallel_cells": sum(1 for v in signs if v == 0),
        "best_3p5_steady": pct_label(best[(3.5, 0.0)][0]), "best_3p5_steady_gain_pct": best[(3.5, 0.0)][1],
        "best_3p5_hard_brake": next(pct_label(best[(3.5, a)][0]) for a in sorted(inputs.map_ax_g) if best[(3.5, a)][0] is not None),
        "pro50_apex_3p5": cell[(inputs.mu, 3.5, 0.0, "+50%")]["ay_change_pct"],
        "anti50_apex_3p5": cell[(inputs.mu, 3.5, 0.0, "-50%")]["ay_change_pct"],
        "pro50_authority_ratio_3p5": cell[(inputs.mu, 3.5, 0.0, "+50%")]["n_delta_per_deg"] / cell[(inputs.mu, 3.5, 0.0, BASELINE)]["n_delta_per_deg"],
        "circle_check_error_pct": s["circle_check"]["error_pct"],
        "coverage_pct": 100.0 * cov["all"], "coverage_points": cov["points"],
        "coverage_demand_pct": 100.0 * cov["demand"], "coverage_radius_max_m": cov["radius_max_m"],
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


def plot_curves(path, tables, track_wheelbase, steer_band, colors):
    fig, ax = plt.subplots(figsize=(6.4, 4.0), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.axvspan(*steer_band, color="#e9e8e4", lw=0)
    ax.text(steer_band[0] + 0.3, 0.03, "mean steer at the limit\nR = 3.5 to 8 m", transform=ax.get_xaxis_transform(), va="bottom", fontsize=8, color=MUTED)
    for name, table in tables.items():
        keep = (table[:, 0] >= 2.0) & (np.degrees(0.5 * (table[:, 1] + table[:, 2])) <= 40.0)
        mean = np.degrees(0.5 * (table[keep, 1] + table[keep, 2]))
        pct = ackermann_pct(table[keep, 1], table[keep, 2], *track_wheelbase[name])
        ax.plot(mean, pct, color=colors[name], lw=2)
        ax.annotate(name, (mean[-1], pct[-1]), xytext=(4, 0), textcoords="offset points", va="center", fontsize=9, color=INK)
    ax.axhline(0.0, color=MUTED, lw=0.8)
    ax.set_xlabel("Mean roadwheel steer (deg)", color=INK)
    ax.set_ylabel("Ackermann, cotangent convention (%)", color=INK)
    ax.set_title("Front steering geometry from hardpoints", color=INK, loc="left", fontsize=11)
    style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_grip(path, rows, base_pct, op_rows, inputs):
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.5, 4.4), facecolor=SURFACE)
    pct = np.array(inputs.ackermann_pct, dtype=float)
    plot_best(right, op_rows, inputs)
    for radius, color in zip(inputs.radii_m, RADIUS_COLORS):
        def series(key, select):
            picked = [next(r.get(key) for r in rows if select(r) and r["radius_m"] == radius and r["curve"] == f"{p:+d}%") for p in inputs.ackermann_pct]
            return np.array([np.nan if v is None else v for v in picked], dtype=float)

        nominal = series("ay_change_pct", is_nominal)
        cases = {(r["tire_case"], r["slip_side"], r["lltd_offset"]) for r in rows}
        spread = np.array([series("ay_change_pct", lambda r, c=c: (r["tire_case"], r["slip_side"], r["lltd_offset"]) == c) for c in cases])
        left.fill_between(pct, np.nanmin(spread, axis=0), np.nanmax(spread, axis=0), color=color, alpha=0.15, lw=0)
        left.plot(pct, nominal, color=color, lw=2, marker="o", ms=4, label=f"R = {radius:g} m")
        design_gain = next(r["ay_change_pct"] for r in rows if is_nominal(r) and r["radius_m"] == radius and r["curve"] == inputs.design)
        left.plot([base_pct[radius]], [design_gain], marker="D", ms=7, color=color, mec=SURFACE, mew=1.5)
    left.axhline(0.0, color=MUTED, lw=0.8)
    left.set_xlabel("Ackermann, cotangent convention (%)", color=INK)
    left.set_ylabel("Max lateral g change vs parallel steer (%)", color=INK)
    left.set_title(f"Apex grip, coasting (band = 12-case range, diamond = {inputs.design})", color=INK, loc="left", fontsize=10)
    style(left)
    left.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def plot_best(ax, op_rows, inputs):
    best = best_laws(op_rows, inputs)
    values = np.array([[np.nan if best[(r, a)][0] is None else best[(r, a)][0] for r in inputs.map_radii_m] for a in inputs.map_ax_g], dtype=float)
    ax.imshow(values, cmap=GAIN_MAP, norm=TwoSlopeNorm(0.0, min(inputs.map_pcts), max(inputs.map_pcts)), aspect="auto", origin="lower")
    for i, a in enumerate(inputs.map_ax_g):
        for j, r in enumerate(inputs.map_radii_m):
            pct, gain = best[(r, a)]
            ax.text(j, i, best_text(pct, gain, "\n"), ha="center", va="center", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(inputs.map_radii_m)), [f"{r:g}" for r in inputs.map_radii_m])
    ax.set_yticks(range(len(inputs.map_ax_g)), [ax_label(a) for a in inputs.map_ax_g])
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=HERE / "study.yml", help="the study inputs, for a variant")
    parser.add_argument("--quick", action="store_true", help="small sweep that runs every code path, for a smoke test")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    out = Path(os.environ["OUT_DIR"])
    vehicle_path = Path(os.environ["BOBSIM_VEHICLE"])
    orion = load_yaml(vehicle_path)
    inputs = load_inputs(args.config, orion, args.quick)
    if bundle_id(vehicle_path) != inputs.bundle:
        raise SystemExit(f"{args.config} names bundle {inputs.bundle}, but the vehicle has {bundle_id(vehicle_path)}.")
    base = with_front(orion, inputs)
    (out / "vehicle_front.yml").write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")

    projection = project_vehicle_yaml(base)
    ggv = types.SimpleNamespace(**{f: getattr(projection.ggv, f) for f in GGV_FIELDS})
    for field, amount in inputs.team_2027.items():
        setattr(ggv, field, amount)
    orion_ggv = project_vehicle_yaml(orion).ggv
    orion_table, _ = steer_table(orion, inputs.rack_mm)
    base_table, _ = steer_table(base, inputs.rack_mm)

    write_csv(out / "steering_curves.csv", [
        {"car": name, "rack_mm": rack, "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
         "ackermann_pct": ackermann_pct(inner, outer, g.track_front, g.wheelbase) if rack >= 2.0 else float("nan")}
        for name, t, g in (("Orion", orion_table, orion_ggv), (inputs.design, base_table, ggv)) for rack, inner, outer in t
    ])

    curves = {BASELINE: ConstantAckermann(0, ggv.track_front, ggv.wheelbase)}
    curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase) for p in inputs.ackermann_pct})
    curves[inputs.design] = TableCurve(base_table)
    outer_max = float(base_table[-1, 2])
    tir_path = tire_templates_root(base) / f"{base['front']['tire']['template']}.tir"
    tir = parse_tir(tir_path)
    nominal_tire = inputs.tire(tir, NOMINAL[0])
    ggv.lltd = balanced_lltd(ggv, nominal_tire, curves[BASELINE], outer_max, inputs)
    nominal_car = Car(ggv, (nominal_tire,) * 4, ggv.lltd, inputs)
    op_cars = {inputs.mu: nominal_car}
    if inputs.mu_sensitivity != inputs.mu:
        sens_tire = {**tir, "LMUY": inputs.mu_sensitivity, "LMUX": inputs.mu_sensitivity, "LKY": inputs.lky_cases[NOMINAL[0]]}
        op_cars[inputs.mu_sensitivity] = Car(ggv, (sens_tire,) * 4, ggv.lltd, inputs)
    steer_targets = {
        pct: needed_mean_steer_deg(nominal_car, ConstantAckermann(pct, ggv.track_front, ggv.wheelbase), inputs.radii_m[0], outer_max)
        for pct in inputs.steer_fix_targets_pct
    }
    biases = (inputs.brake_bias, float(base["brake"]["front_bias"]))
    cases = build_cases(inputs, ggv, tir, curves, outer_max, biases, orion, steer_targets, nominal_car)
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
    corners = track_corners(line, inputs.corner_radius_max_m)
    braking_limit = {f"{b:.2f}": braking_limit_g(nominal_car, b) for b in biases}

    op_curves = {name: curves[name] for name in (BASELINE, *(f"{p:+d}%" for p in inputs.map_pcts), inputs.design)}
    kinetic = base["powertrain"]["pDriveline"]["diff_kineticFrictionRatio"]
    rears = {REAR_BASELINE: (0.0, 0.0, kinetic), "LSD": inputs.planned_diff + (kinetic,)}
    brakes = {f"brakes {b:.2f}": b for b in biases}
    brake_nominal = f"brakes {inputs.brake_bias:.2f}"
    rear_radius = float(base["rear"]["wheel"]["radius_m"])
    lky = inputs.lky_cases["lky_scaled"]
    sens_cars = {f"LLTD {d:+.2f}": Car(ggv, (nominal_tire,) * 4, ggv.lltd + d, inputs) for d in inputs.variant_lltd_offsets}
    sens_cars[f"LKY {lky:.2f}"] = Car(ggv, ({**nominal_tire, "LKY": lky},) * 4, ggv.lltd, inputs)

    def map_case(car, radius, ax, model, labels, tol=2e-5):
        lon = Longitudinal(-ax, bias=brakes[model]) if ax < 0.0 else Longitudinal(-ax, diff=rears[model], wheel_radius=rear_radius)
        baseline = labels == {"mu": inputs.mu} and model in (REAR_BASELINE, brake_nominal)
        return {"kind": "map", "car": car, "curves": op_curves, "outer_max": outer_max, "radius": radius, "lon": lon, "tol": tol,
                "probe": baseline, "deriv": baseline and ax == 0.0 and radius in inputs.deriv_radii_m,
                "grip_probe": inputs.grip_probe, "deriv_fraction": inputs.deriv_fraction,
                "labels": {**labels, "rear": model, "radius_m": radius, "ax_g": ax}}

    def models(ax, nominal):
        if ax < 0.0:
            return list(brakes) if nominal else [brake_nominal]
        return list(rears) if nominal else [REAR_BASELINE]

    op_cases = [
        map_case(car, radius, ax, model, {"mu": mu})
        for mu, car in op_cars.items() for radius in inputs.map_radii_m for ax in inputs.map_ax_g for model in models(ax, mu == inputs.mu)
    ]
    sens_cases = [
        map_case(car, radius, ax, model, {"mu": inputs.mu, "variant": variant}, inputs.variant_tol_g)
        for variant, car in sens_cars.items() for radius in inputs.variant_radii_m for ax in inputs.variant_ax_g for model in models(ax, False)
    ]
    map_results = map_cases(solve_case, op_cases + sens_cases)
    op_rows = [row for result in map_results[:len(op_cases)] for row in result]
    sens_rows = [row for result in map_results[len(op_cases):] for row in result]
    for r in op_rows + sens_rows:
        r["lock_limited"] = bool(math.isfinite(r["ay_max_g"]) and r["lock_margin_deg"] < inputs.lock_margin_min_deg)
    base_rows = [r for r in op_rows if r["rear"] in (REAR_BASELINE, brake_nominal)]
    design_rows = [r for r in op_rows + sens_rows if r["ax_g"] in inputs.variant_ax_g]
    robust = {
        "all": robust_region(design_rows, inputs.robust_tol_pct),
        "beyond_3p5": robust_region([r for r in design_rows if r["radius_m"] > inputs.radii_m[0]], inputs.robust_tol_pct),
    }
    power_w = float(load_yaml(repo_root() / inputs.lap_config)["event"]["drive_power_limit_w"])

    def lap_limits(car, bias):
        return {"brake_g": braking_limit_g(car, bias)["limit_g"], "drive_g": exit_accel_g(car, ggv.max_drive_force),
                "mass": car.mass, "power_w": power_w}

    qss_cases, caps = [], []
    for mu, car in op_cars.items():
        limits = lap_limits(car, inputs.brake_bias)
        rows_mu = [r for r in base_rows if r["mu"] == mu]
        for name in op_curves:
            caps += [{"mu": mu, **c} for c in check_map(rows_mu, name, limits["brake_g"], limits["drive_g"], inputs)]
            qss_cases.append({"kind": "qss", "line": line["line"], "rows": rows_mu, "inputs": inputs, **limits, "labels": {"mu": mu, "curve": name}})
    refined = [{**c, "refine": True} for c in qss_cases if c["labels"]["mu"] == inputs.mu and c["labels"]["curve"] in (BASELINE, *inputs.convergence_curves)]
    fill_qss = cap_fill_cases(qss_cases, base_rows, caps, inputs)
    qss_results = map_cases(solve_case, qss_cases + refined + fill_qss)
    nominal_laps = qss_results[:len(qss_cases)]
    refined_laps = qss_results[len(qss_cases):len(qss_cases) + len(refined)]
    fill_laps = qss_results[len(qss_cases) + len(refined):]
    laps = lap_phases(nominal_laps, line["curvature"], base_rows, inputs)
    lap_time = {(r["curve"], r["refine"]): r["lap_time_s"] for r in nominal_laps + refined_laps if r["mu"] == inputs.mu}
    convergence = {}
    for name in inputs.convergence_curves:
        coarse = lap_time[(name, False)] - lap_time[(BASELINE, False)]
        fine = lap_time[(name, True)] - lap_time[(BASELINE, True)]
        convergence[name] = {"delta_s": round(coarse, 4), "delta_refined_s": round(fine, 4), "change_s": round(fine - coarse, 4)}
    convergence["band_s"] = round(2.0 * max(abs(v["change_s"]) for v in convergence.values()), 4)
    reference_lap = next(r for r in nominal_laps if r["mu"] == inputs.mu and r["curve"] == BASELINE)
    coverage = trim_coverage(reference_lap, line["curvature"], nominal_car, op_curves[BASELINE], outer_max, rears[REAR_BASELINE],
                             rear_radius, inputs.brake_bias, inputs)
    nominal_limits = next(c for c in qss_cases if c["labels"] == {"mu": inputs.mu, "curve": BASELINE})
    circle = circle_check(nominal_limits["rows"], *(nominal_limits[k] for k in ("brake_g", "drive_g", "mass", "power_w")), inputs)
    for f in fix:
        f.pop("table")
    for name, data in (("limits", rows), ("braking", braking), ("toe", toe), ("mass_cg", mass), ("operating_map", op_rows),
                       ("operating_map_sensitivity", sens_rows)):
        write_csv(out / f"{name}.csv", data)
    write_csv(out / "robust.csv", [
        {"law": law, "worst_shortfall_pct": v["worst_shortfall_pct"], "binding": v["binding"], "in_region_0p5": v["in_region"]["0.5"]}
        for law, v in robust["all"].items()
    ])

    nominal_base = {r["radius_m"]: r for r in rows if r["curve"] == inputs.design and is_nominal(r)}
    base_pct = {radius: r["ackermann_pct_at_limit"] for radius, r in nominal_base.items()}
    steer_band = (
        min(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_base.values() if r["radius_m"] <= 8.0),
        max(0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_base.values() if r["radius_m"] <= 8.0),
    )
    tables = {"Orion": orion_table, inputs.design: base_table}
    geometry = {"Orion": (orion_ggv.track_front, orion_ggv.wheelbase), inputs.design: (ggv.track_front, ggv.wheelbase)}
    plot_curves(out / "ackermann_curves.png", tables, geometry, steer_band, {inputs.design: DESIGN_COLOR, "Orion": ORION_COLOR})
    plot_grip(out / "grip_vs_ackermann.png", rows, base_pct, base_rows, inputs)

    table_rows = {}
    for radius in inputs.radii_m:
        table_rows[f"{radius:g}m"] = {
            r["curve"]: {"ay_change_pct": round(r["ay_change_pct"], 2), "limiting_axle": r.get("limiting_axle")}
            for r in rows if is_nominal(r) and r["radius_m"] == radius
        }
    spread = {}
    for radius in inputs.radii_m:
        for p in inputs.ackermann_pct:
            values = [r["ay_change_pct"] for r in rows if r["radius_m"] == radius and r["curve"] == f"{p:+d}%"]
            spread.setdefault(f"{radius:g}m", {})[f"{p:+d}%"] = [round(float(np.nanmin(values)), 2), round(float(np.nanmax(values)), 2)]
    slip_norm = [r for r in braking if r["combined_slip"] == "slip_norm"]
    ellipse = [r for r in braking if r["combined_slip"] == "ellipse"]

    summary = {
        "vehicle": {
            "note": f"{inputs.design} front hardpoints, team 2027 mass and CG, balanced LLTD, Orion rear and tire",
            "mass_kg": round(ggv.mass, 2), "cg_height_m": round(ggv.cg_height, 4),
            "front_static_frac": round(ggv.front_static_frac, 4), "wheelbase_m": round(ggv.wheelbase, 4),
            "track_front_m": round(ggv.track_front, 4), "track_rear_m": round(ggv.track_rear, 4),
            "lltd_front": round(ggv.lltd, 4), "lltd_source": f"balanced at R = {inputs.balance_radius_m:g} m, held at every grip scale",
        },
        "effective_inputs": {
            "mass_kg": round(ggv.mass, 2), "cg_height_m": round(ggv.cg_height, 4), "front_static_frac": ggv.front_static_frac,
            "wheelbase_m": round(ggv.wheelbase, 4), "track_front_m": round(ggv.track_front, 4), "track_rear_m": round(ggv.track_rear, 4),
            "lltd_front": round(ggv.lltd, 4), "grip_scale": [inputs.mu, inputs.mu_sensitivity], "LKY": sorted(inputs.lky_cases.values()),
            "brake_bias_front": list(biases), "rear_models": {k: {"preload_nm": v[0], "lock": v[1], "kinetic": v[2]} for k, v in rears.items()},
            "rear_wheel_radius_m": rear_radius, "drive_force_cap_n": round(ggv.max_drive_force, 1), "power_w": power_w, "aero": "none",
            "static_toe_deg": inputs.static_toe_deg, "static_camber_deg": inputs.static_camber_deg, "vehicle_front_yml": "front geometry only",
        },
        "tire": {"template": base["front"]["tire"]["template"], "sha256": hashlib.sha256(tir_path.read_bytes()).hexdigest(),
                 "LMUY_LMUX": inputs.mu, "LKY_cases": inputs.lky_cases},
        "ackermann_pct_at_rack": {
            name: {f"{r:g}mm": round(float(ackermann_pct(at_rack(t, r, 1), at_rack(t, r, 2), *geometry[name])), 1) for r in inputs.report_rack_mm}
            for name, t in tables.items()
        },
        "nominal": table_rows,
        "ay_change_pct_range_all_cases": spread,
        "brake_model_gap_pts": max_change_gap(slip_norm, ellipse, ("brake_bias_front", "brake_g", "radius_m", "curve"), inputs.brake_curves),
        "mass_gap_pts": max_change_gap(mass, [r for r in rows if is_nominal(r)], ("radius_m", "curve"), inputs.mass_curves),
        "toe": toe_summary(toe),
        "team_2027_inputs": {
            "mass_kg": round(inputs.team_2027["mass"], 1), "cg_height_m": round(inputs.team_2027["cg_height"], 4),
            "front_static_frac": inputs.team_2027["front_static_frac"], "straight_braking_limit": braking_limit,
        },
        "lock_at_rack_travel": lock_check(base_table, rows, nominal_car, curves[inputs.design], outer_max, float(tir["WIDTH"]), inputs),
        "front_base_linkage": {"arm_offset_mm": arm_offset_mm(base), "inner_wheel_at_travel": lock_geometry(base, inputs.rack_travel_mm, inputs.bump_mm)},
        "steering_fix": fix,
        "first_principles_optimum": optimum_check(nominal_car, rows, inputs),
        "track_minimum_curvature_line": {
            "track": line["track"], "length_m": round(line["length_m"], 1), "corners_under_15m": len(corners),
            "corners": [{"turn_deg": round(math.degrees(t), 1), "min_radius_m": round(r, 2)} for t, r in corners],
        },
        "operating_map": {
            "radii_m": inputs.map_radii_m, "ax_g": inputs.map_ax_g, "curves": list(op_curves), "mu": list(op_cars),
            "rear_models": list(rears) + list(brakes), "rear_baseline": REAR_BASELINE, "variants": list(sens_cars),
            "variant_radii_m": inputs.variant_radii_m, "variant_ax_g": inputs.variant_ax_g,
            "lltd_front": {f"{mu:g}": round(car.lltd, 4) for mu, car in op_cars.items()},
            "law_no_grip_cells": sum(1 for r in base_rows if not math.isfinite(r["ay_max_g"])),
            "caps": caps,
            "lock_limited_cells": [{k: r[k] for k in ("mu", "radius_m", "ax_g", "curve", "ay_max_g") if k in r} | ({"variant": r["variant"]} if "variant" in r else {})
                                   for r in op_rows + sens_rows if r["lock_limited"]],
            "lock_margin_min_deg": {name: round(min(r["lock_margin_deg"] for r in base_rows if r["curve"] == name and math.isfinite(r["ay_max_g"])), 2)
                                    for name in op_curves},
        },
        "robust": robust,
        "lsd_rows": [{k: r[k] for k in ("radius_m", "ax_g", "curve", "ay_max_g", "ay_change_pct")} for r in op_rows if r["rear"] == "LSD"],
        "lap_qss": laps,
        "circle_check": circle,
        "grid_convergence": convergence,
        "cap_check": cap_check_result(nominal_laps, fill_laps, inputs),
        "trim_coverage": coverage,
        "clipped_braking": clipped_braking(base_rows),
    }
    (out / "summary.json").write_text(json.dumps(finite(summary), indent=2, allow_nan=False), encoding="utf-8")
    write_readme_parts(out, json.loads(json.dumps(finite(summary))), base_rows, inputs)


if __name__ == "__main__":
    main()
