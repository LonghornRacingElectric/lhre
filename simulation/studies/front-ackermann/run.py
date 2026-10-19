import argparse
import copy
import dataclasses
import hashlib
import csv
import json
import math
import os
import sys
import types
from pathlib import Path
from typing import Optional

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
from tools.bundle import BUNDLE, REPO, file_sha256, load_bundle
from tools.parallel import map_cases
from tools.study_inputs import StudyInputsError, load_study_inputs, markdown_table, resolve, write_resolved
from tools.support import Observation, SupportPolicy, cell_best, compare, gain_pct
from tools.tire_support import TireSupport
from tools.trim_status import LimitResult, TrimRecord, TrimStatus, bisect_limit, combine

G = 9.80665
BASELINE = "+0%"
DESIGN = "Front V34"
RADII_M = (3.5, 4.5, 6.0, 8.0, 15.0)
ACKERMANN_PCT = (-50, -25, 0, 25, 50, 75, 100)
SLIP_SIDES = {"pos": 1.0, "neg": -1.0}
LLTD_OFFSETS = (-0.10, 0.0, 0.10)
NOMINAL = ("lky_1", "pos", 0.0)
GRIP_PROBE = 1.01
RACK_MM = np.arange(0.0, 60.5, 0.5)
REPORT_RACK_MM = (10.0, 20.0)
BUMP_MM = 25.0
BRAKE_G = (0.3, 0.5)
BRAKE_RADII_M = (3.5, 4.5)
BRAKE_MODELS = ("ellipse", "slip_norm")
BRAKE_CURVES = (DESIGN, "-50%", "-25%", "+25%", "+50%", "+75%", "+100%")
FX_MAX_FZ_N = np.arange(0.0, 2525.0, 25.0)
TOE_OUT_DEG = (-1.0, -0.5, 0.0, 0.5, 1.0)
TOE_RADII_M = (3.5, 4.5)
TOE_ACKERMANN_PCT = tuple(range(0, 101, 10))
MASS_CURVES = (DESIGN, "-50%", "+50%", "+75%")
MASS_CASES = (
    ("mass +10 kg", "mass", 10.0), ("mass -10 kg", "mass", -10.0),
    ("CG +20 mm", "cg_height", 0.020), ("CG -20 mm", "cg_height", -0.020),
    ("front +3 pts", "front_static_frac", 0.03), ("front -3 pts", "front_static_frac", -0.03),
    ("camber variant", "camber", None),
)
ANTI_LINKAGE_PCT = -40
STEER_FIX_TARGETS_PCT = (float(ANTI_LINKAGE_PCT), 0.0, 70.0)
STEER_RESERVE = 0.9
LAP_CONFIG = "simulations/lap/lap_time_eval_config.yml"
CORNER_RADIUS_MAX_M = 15.0
MAP_RADII_M = (3.5, 4.5, 6.0, 8.0, 11.0, 15.0)
MAP_AX_G = (-1.2, -0.9, -0.6, -0.3, 0.0, 0.15, 0.3, 0.6, 0.9)
MAP_PCTS = (-50, -25, 25, 50, 75, 100)
BEST_MIN_GAIN_PCT = 0.1
DERIV_RADII_M = (3.5, 4.5, 8.0)
LIMIT_CURVES = ("-50%", "+0%", "+50%", DESIGN)
DERIV_FRACTION = 0.9
QSS_SPEEDS_MPS = tuple(np.arange(3.0, 16.5, 0.5)) + (17.0, 18.0, 19.0, 20.0, 22.0, 25.0, 30.0, 35.0)
QSS_AY_STEP_G = 0.001
CIRCLE_CHECK_RADIUS_M = 8.0
CIRCLE_CHECK_TOL = 0.005
CONVERGENCE_CURVES = ("-50%", "+50%", "+75%", "+100%", DESIGN)
REAR_BASELINE = "open"
VARIANT_RADII_M = (3.5, 4.5, 6.0, 8.0)
VARIANT_AX_G = (-0.6, -0.3, 0.0, 0.3, 0.6)
VARIANT_TOL = 5e-4
RESIDUAL_TOL = 1e-7
STATIONARY_TOL = 1e-8
LIFT_FZ_N = 1e-3
CORNERS = ("fl", "fr", "rl", "rr")
UNKNOWN_BOUNDS = ("beta_bound", "steer_bound", "lon_force_bound")
HAIRPIN_OUTSIDE_DIAMETER_M = 9.0
LOCK_MARGIN_MIN_DEG = 0.5
COVERAGE_STEP = 5
COVERAGE_DEMAND = 0.98
SKIDPAD_RADIUS_M = 9.125
LAP_RADIUS_BANDS_M = (6.0, 15.0)
GGV_FIELDS = ("mass", "wheelbase", "cg_height", "track_front", "track_rear", "front_static_frac", "lltd", "max_drive_force")

INPUTS_FILE = Path(__file__).with_name("inputs.yml")
VEHICLE_BUNDLE = REPO / BUNDLE
INPUT_SPEC = {
    "steering.rack_travel": "mm",
    "mass_properties.mass": "kg",
    "mass_properties.cg_height": "m",
    "mass_properties.front_static_frac": "1",
    "alignment.static_camber": "deg",
    "alignment.static_toe": "deg",
    "alignment.camber_variant": "deg",
    "lltd.balance_radius": "m",
    "tire.grip_scale": "1",
    "tire.grip_scale_sensitivity": "1",
    "brake.front_bias_nominal": "1",
    "differential.planned_lsd_preload": "N m",
    "differential.planned_lsd_lock": "1",
    "differential.braking_coupling": None,
    "policy.min_coverage": "1",
    "policy.ay_floor": "g",
    "policy.region_tolerances": "pct",
}

RADIUS_COLORS = ("#0d366b", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef")
GAIN_MAP = LinearSegmentedColormap.from_list("gain", ("#e34948", "#f0efec", "#2a78d6"))
CAR_COLORS = {DESIGN: "#2a78d6", "Orion": "#eb6834"}
INK, MUTED, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


QUICK = {
    "lltd_offsets": (0.0,), "slip_sides": {"pos": 1.0}, "brake_g": (0.5,), "brake_radii_m": (3.5,),
    "toe_out_deg": (0.0,), "toe_ackermann_pct": (0, 50, 100), "mass_cases": MASS_CASES[:1],
    "map_radii_m": (3.5, 4.5, 8.0, 15.0), "map_ax_g": (-0.6, 0.0, 0.3), "deriv_radii_m": (3.5,),
    "variant_radii_m": (3.5, 8.0), "variant_ax_g": (-0.6, 0.0, 0.3),
}


@dataclasses.dataclass(frozen=True)
class Inputs:
    mu: float
    mu_sensitivity: float
    brake_bias: float
    rack_travel_mm: float
    static_camber_deg: float
    camber_variant_deg: float
    balance_radius_m: float
    planned_diff: tuple
    mass_properties: dict
    policy: SupportPolicy
    ackermann_pct: tuple = ACKERMANN_PCT
    steer_fix_targets_pct: tuple = STEER_FIX_TARGETS_PCT
    lltd_offsets: tuple = LLTD_OFFSETS
    slip_sides: dict = dataclasses.field(default_factory=lambda: dict(SLIP_SIDES))
    brake_g: tuple = BRAKE_G
    brake_radii_m: tuple = BRAKE_RADII_M
    toe_out_deg: tuple = TOE_OUT_DEG
    toe_ackermann_pct: tuple = TOE_ACKERMANN_PCT
    mass_cases: tuple = MASS_CASES
    map_radii_m: tuple = MAP_RADII_M
    map_ax_g: tuple = MAP_AX_G
    deriv_radii_m: tuple = DERIV_RADII_M
    variant_radii_m: tuple = VARIANT_RADII_M
    variant_ax_g: tuple = VARIANT_AX_G

    @property
    def lky_cases(self):
        return {"lky_1": 1.0, "lky_scaled": self.mu}

    def tire(self, tir, lky_name):
        return {**tir, "LMUY": self.mu, "LMUX": self.mu, "LKY": self.lky_cases[lky_name]}


@dataclasses.dataclass(frozen=True)
class SlipNormPoint:
    fy: float
    use: float
    kappa: float
    evaluated: Optional[tuple]


class Car:
    def __init__(self, ggv, tires, lltd, support, camber_deg):
        self.mass, self.wheelbase, self.cg_height = ggv.mass, ggv.wheelbase, ggv.cg_height
        self.track_front, self.track_rear = ggv.track_front, ggv.track_rear
        self.front_frac, self.lltd, self.tires = ggv.front_static_frac, lltd, tires
        self.support = support
        a = self.wheelbase * (1.0 - self.front_frac)
        b = self.wheelbase * self.front_frac
        self.corners = (
            (a, self.track_front / 2), (a, -self.track_front / 2),
            (-b, self.track_rear / 2), (-b, -self.track_rear / 2),
        )
        self.combined = "ellipse"
        c = math.radians(camber_deg)
        self.camber = (c, -c, c, -c)
        inside = FX_MAX_FZ_N[(FX_MAX_FZ_N > support.fz[0]) & (FX_MAX_FZ_N < support.fz[1])]
        self.fz_table = np.unique(np.r_[support.fz[0], inside, support.fz[1]])
        kappa_bounds = {-1: support.search_bounds("kappa", -0.5, 0.0), 1: support.search_bounds("kappa", 0.0, 0.5)}
        self.kappa_limit = {sign: abs(kappa_bounds[sign][0 if sign < 0 else 1]) for sign in (-1, 1)}
        alpha_bounds = support.search_bounds("alpha", 0.0, 0.6)
        self.fx_max_n, self.kappa_peak, self.alpha_peak, self.boundary_peaks = {-1: [], 1: []}, {-1: [], 1: []}, [], []
        for axle, t in (("front", tires[0]), ("rear", tires[2])):
            for sign in (-1, 1):
                fx = [minimize_scalar(lambda k: -abs(_mf52_fx_pure(t, fz, k, 0.0)), bounds=kappa_bounds[sign], method="bounded")
                      for fz in self.fz_table]
                self.fx_max_n[sign].append(np.array([abs(r.fun) for r in fx]))
                self.kappa_peak[sign].append(np.array([abs(r.x) for r in fx]))
                if any(support.at_edge("kappa", r.x) for r in fx):
                    self.boundary_peaks.append(f"kappa_{'drive' if sign > 0 else 'brake'}:{axle}")
            fy = [minimize_scalar(lambda a: -lateral_n(t, fz, a, 1.0), bounds=alpha_bounds, method="bounded") for fz in self.fz_table]
            self.alpha_peak.append(np.array([r.x for r in fy]))
            if any(support.at_edge("alpha", r.x) for r in fy):
                self.boundary_peaks.append(f"alpha:{axle}")

    def loads(self, ay_g, decel_g=0.0):
        weight = self.mass * G
        shift = weight * decel_g * self.cg_height / self.wheelbase
        axles = [weight * self.front_frac + shift, weight * (1.0 - self.front_frac) - shift]
        for i in (0, 1):
            if axles[i] < 0.0:
                axles[i], axles[1 - i] = 0.0, weight
        tracks = (self.track_front, self.track_rear)
        roll = weight * ay_g * self.cg_height
        moments = [self.lltd * roll, (1.0 - self.lltd) * roll]
        capacity = [axles[i] / 2 * tracks[i] for i in (0, 1)]
        capped = [False, False]
        for i in (0, 1, 0):
            if abs(moments[i]) > capacity[i]:
                excess = moments[i] - math.copysign(capacity[i], moments[i])
                moments[i] -= excess
                capped[i] = True
                if not capped[1 - i]:
                    moments[1 - i] += excess
        fz, lifted = [], []
        for i in (0, 1):
            transfer = moments[i] / tracks[i]
            inner, outer = axles[i] / 2 - transfer, axles[i] / 2 + transfer
            fz += [max(inner, 0.0), max(outer, 0.0)]
            lifted += [capped[i] and transfer > 0.0 or axles[i] == 0.0, capped[i] and transfer < 0.0 or axles[i] == 0.0]
        return tuple(fz), tuple(lifted)

    def peak(self, table, corner, fz):
        return float(np.interp(fz, self.fz_table, table[corner // 2]))

    def lateral(self, corner, fz, alpha, fx_w, side):
        tire = self.tires[corner]
        if fz <= LIFT_FZ_N:
            return 0.0, (math.inf if fx_w else 0.0)
        gamma = self.camber[corner]
        if fx_w == 0.0:
            return lateral_n(tire, fz, alpha, side, gamma), 0.0
        sign = 1 if fx_w > 0.0 else -1
        if self.combined == "ellipse":
            limit = self.peak(self.fx_max_n[sign], corner, fz)
            use = abs(fx_w) / limit if limit > 0.0 else math.inf
            return lateral_n(tire, fz, alpha, side, gamma) * math.sqrt(max(0.0, 1.0 - use * use)), use
        point = self.slip_norm_point(corner, fz, alpha, fx_w, side)
        return point.fy, point.use

    def slip_norm_point(self, corner, fz, alpha, fx_w, side):
        tire, gamma = self.tires[corner], self.camber[corner]
        sign = 1 if fx_w > 0.0 else -1
        kp, ap = self.peak(self.kappa_peak[sign], corner, fz), self.peak(self.alpha_peak, corner, fz)
        sy = math.tan(alpha) / math.tan(ap)

        def forces(kappa):
            sx = kappa / kp
            s = math.hypot(sx, sy)
            if s < 1e-12:
                return 0.0, 0.0
            fx = abs(_mf52_fx_pure(tire, fz, sign * s * kp, 0.0)) * abs(sx) / s
            return fx, lateral_n(tire, fz, math.atan(s * math.tan(ap)), side, gamma) * sy / s

        best = minimize_scalar(lambda k: -forces(k)[0], bounds=(0.0, self.kappa_limit[sign]), method="bounded", options={"xatol": 1e-6})
        capacity = -best.fun
        if abs(fx_w) >= capacity:
            kappa = best.x
        else:
            kappa = brentq(lambda k: forces(k)[0] - abs(fx_w), 0.0, best.x, xtol=1e-9)
        s = math.hypot(kappa / kp, sy)
        evaluated = (sign * s * kp, side * math.atan(s * math.tan(ap))) if s >= 1e-12 else None
        return SlipNormPoint(forces(kappa)[1], abs(fx_w) / capacity, sign * kappa, evaluated)

    def with_grip(self, front=1.0, rear=1.0):
        probe = copy.copy(self)
        f = {**self.tires[0], "LMUY": self.tires[0]["LMUY"] * front}
        r = {**self.tires[2], "LMUY": self.tires[2]["LMUY"] * rear}
        probe.tires = (f, f, r, r)
        return probe

    def implied_kappa(self, corner, fz, fx_w):
        if fx_w == 0.0 or fz <= LIFT_FZ_N:
            return 0.0
        sign = 1 if fx_w > 0.0 else -1
        tire, peak = self.tires[corner], self.peak(self.kappa_peak[sign], corner, fz)

        def gap(k):
            return abs(_mf52_fx_pure(tire, fz, sign * k, 0.0)) - abs(fx_w)

        if gap(0.0) >= 0.0:
            return 0.0
        if gap(peak) < 0.0:
            return sign * peak
        return sign * brentq(gap, 0.0, peak, xtol=1e-9)

    def wheel_states(self, s, side):
        wheels = []
        for i, name in enumerate(CORNERS):
            fz, alpha, fx_w = s["loads"][i], s["alphas"][i], s["wheel_fx"][i]
            point = None
            if self.combined == "slip_norm" and fx_w != 0.0 and fz > LIFT_FZ_N:
                point = self.slip_norm_point(i, fz, alpha, fx_w, side)
            kappa = s["kappas"][i]
            if kappa is None:
                kappa = self.implied_kappa(i, fz, fx_w) if point is None else point.kappa
            gamma = side * self.camber[i]
            if s["lifted"][i] or fz <= LIFT_FZ_N:
                flags = (f"wheel_lift:{name.upper()}",)
            else:
                flags = self.support.violations(fz, side * alpha, kappa, gamma, name.upper())
                if point is not None and point.evaluated is not None:
                    fx_slip, fy_slip = point.evaluated
                    flags += tuple(f.replace("_out_of_fit", "_eval_out_of_fit")
                                   for f in self.support.violations(fz, fy_slip, fx_slip, gamma, name.upper())
                                   if f.startswith(("alpha_", "kappa_")))
            wheels.append({"corner": name, "fz_n": fz, "alpha_rad": alpha, "kappa": kappa, "gamma_rad": gamma,
                           "use": s["brake_use"][i], "flags": flags})
        return tuple(wheels)


def with_front(vehicle, geometry, tie_o_dy_m=0.0, tie_o_dx_m=0.0, rack_dy_m=0.0, rack_dz_m=0.0):
    base = copy.deepcopy(vehicle)
    base["front"]["suspension"].update(copy.deepcopy(geometry.points_m))
    base["front"]["suspension"]["tie_o_m"][0] += tie_o_dx_m
    base["front"]["suspension"]["tie_o_m"][1] += tie_o_dy_m
    pickup = geometry.rack_pickup_m
    base["front"]["steering"]["rack_pickup_m"] = [pickup[0], pickup[1] + rack_dy_m, pickup[2] + rack_dz_m]
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


def to_rack_stop(table, rack_mm):
    if rack_mm >= table[-1, 0]:
        return table, "table_end"
    stop = [rack_mm, at_rack(table, rack_mm, 1), at_rack(table, rack_mm, 2)]
    return np.vstack([table[table[:, 0] < rack_mm], stop]), "rack_stop"


class Longitudinal:
    def __init__(self, decel_g, bias=None, diff=None, wheel_radius=None):
        self.decel_g, self.bias, self.diff, self.wheel_radius = decel_g, bias, diff, wheel_radius
        coupled = diff is not None and (diff[0] != 0.0 or diff[1] != 0.0)
        self.withheld = decel_g > 0.0 and coupled
        if diff is None:
            self.coupling = "open_axle"
        else:
            self.coupling = "lsd" if coupled else "open_diff"

    def wheel_fx(self, total_n, car=None, loads=None, speeds=None):
        if self.withheld:
            raise NotModeledError("Braking through a coupled differential is not modeled. The study withholds it.")
        if self.diff is not None:
            fx, kappas = lsd_split(total_n, self.diff, self.wheel_radius, car, loads[2:], speeds)
            return (0.0, 0.0) + fx, (None, None) + kappas
        front, rear = -self.bias * total_n / 2, -(1.0 - self.bias) * total_n / 2
        return (front, front, rear, rear), (None,) * 4


class NotModeledError(RuntimeError):
    pass


def lsd_split(total_n, diff, radius, car, loads, speeds):
    preload, lock, kinetic = diff
    static = 2.0 * preload / radius + lock * total_n
    if static == 0.0:
        return (total_n / 2, total_n / 2), (None, None)
    tire = car.tires[2]

    def kappas(wheel_speed):
        return tuple((wheel_speed - v) / v for v in speeds)

    def wheel_fx(wheel_speed):
        return tuple(_mf52_fx_pure(tire, fz, k, 0.0) for fz, k in zip(loads, kappas(wheel_speed)))

    grid = np.linspace(min(speeds), 1.25 * max(speeds), 9)
    gap = [sum(wheel_fx(w)) - total_n for w in grid]
    k = next((k for k in range(len(grid) - 1) if gap[k] <= 0.0 < gap[k + 1]), None)
    if k is not None:
        speed = brentq(lambda w: sum(wheel_fx(w)) - total_n, grid[k], grid[k + 1], xtol=1e-6)
        locked = wheel_fx(speed)
        if abs(locked[0] - locked[1]) <= static:
            return locked, kappas(speed)
    slip = math.copysign(kinetic * static / 2, speeds[1] - speeds[0])
    return (total_n / 2 + slip, total_n / 2 - slip), (None, None)


class SteeringDomainError(ValueError):
    pass


class SteeringLaw:
    def __init__(self, domain, bound):
        self.domain, self.bound = (float(domain[0]), float(domain[1])), bound
        if not self.domain[0] < self.domain[1]:
            raise ValueError(f"A steering law needs a non-empty outer-angle domain. Got {self.domain} rad.")

    def __call__(self, outer):
        lo, hi = self.domain
        if not lo - 1e-12 <= outer <= hi + 1e-12:
            raise SteeringDomainError(
                f"Outer angle {math.degrees(outer):.4f} deg is outside the law domain "
                f"{math.degrees(lo):.4f} to {math.degrees(hi):.4f} deg ({self.bound}).")
        return self.inner(outer)


class ToeCurve(SteeringLaw):
    def __init__(self, curve, toe_out_deg):
        self.curve, self.half = curve, math.radians(toe_out_deg) / 2
        super().__init__((curve.domain[0] - self.half, curve.domain[1] - self.half), curve.bound)

    def inner(self, outer):
        return self.curve(outer + self.half) + self.half


class ConstantAckermann(SteeringLaw):
    def __init__(self, pct, track, wheelbase, outer_max, bound="table_end"):
        self.k = pct / 100.0 * track / wheelbase
        if self.k > 0.0 and math.atan(1.0 / self.k) <= outer_max:
            outer_max, bound = math.atan(1.0 / self.k) - 1e-6, "inner_wheel_90deg"
        super().__init__((0.0, outer_max), bound)

    def inner(self, outer):
        return math.atan2(math.tan(outer), 1.0 - self.k * math.tan(outer))


class TableCurve(SteeringLaw):
    def __init__(self, table, bound="table_end"):
        self.outer, self.inner_table = table[:, 2].copy(), table[:, 1].copy()
        if np.any(np.diff(self.outer) <= 0.0):
            raise ValueError("A steering table needs a strictly increasing outer angle.")
        super().__init__((self.outer[0], self.outer[-1]), bound)

    def inner(self, outer):
        return float(np.interp(outer, self.outer, self.inner_table))


def lateral_n(tire, fz, alpha, side, gamma=0.0):
    return -side * _mf52_fy_pure(tire, fz, side * alpha, side * gamma)


def state(car, curve, radius, side, beta, outer, ay_g, lon=None, lon_n=0.0):
    speed = math.sqrt(ay_g * G * radius)
    yaw_rate = speed / radius
    steer = (curve(outer), outer, 0.0, 0.0)
    decel_g = lon.decel_g if lon else 0.0
    ax_body = -decel_g * math.cos(beta) - ay_g * math.sin(beta)
    ay_body = -decel_g * math.sin(beta) + ay_g * math.cos(beta)
    loads, lifted = car.loads(ay_body, -ax_body)
    rear_speeds = tuple(speed * math.cos(beta) - yaw_rate * y for x, y in car.corners[2:])
    wheel_fx, kappas = lon.wheel_fx(lon_n, car, loads, rear_speeds) if lon else ((0.0,) * 4, (None,) * 4)
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
        "speed": speed, "steer": steer, "loads": loads, "lifted": lifted, "alphas": alphas, "forces": forces,
        "brake_use": uses, "wheel_fx": wheel_fx, "kappas": kappas, "fx": fx, "fy": fy, "mz": mz,
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


def past_peak_axles(car, s, side, eps=1e-4):
    rise = [
        car.lateral(i, fz, a + eps, fx_w, side)[0] - f
        for i, (fz, a, fx_w, f) in enumerate(zip(s["loads"], s["alphas"], s["wheel_fx"], s["forces"]))
    ]
    floor = -1e-6 * eps * car.mass * G
    return tuple(axle for axle, gain in (("front", rise[0] + rise[1]), ("rear", rise[2] + rise[3])) if gain < floor)


def classify(car, curve, radius, side, ay_g, lon, res):
    unknowns = tuple(float(v) for v in res.x)
    active = tuple(dict.fromkeys(UNKNOWN_BOUNDS[i] for i, a in enumerate(res.active_mask) if a))
    if not np.all(np.isfinite(res.fun)):
        return TrimRecord(TrimStatus.NUMERICAL_FAILURE, ("non_finite",), None, 1, unknowns, active)
    residual = float(np.max(np.abs(res.fun)))
    if residual >= RESIDUAL_TOL:
        if res.status <= 0:
            reason = "max_evaluations" if res.status == 0 else "solver_error"
            return TrimRecord(TrimStatus.NUMERICAL_FAILURE, (reason,), residual, 1, unknowns, active)
        if res.optimality > STATIONARY_TOL:
            return TrimRecord(TrimStatus.NUMERICAL_FAILURE, ("not_converged",), residual, 1, unknowns, active)
        if active:
            return TrimRecord(TrimStatus.DOMAIN_REJECTED, active, residual, 1, unknowns, active)
    s = state(car, curve, radius, side, unknowns[0], unknowns[1], ay_g, lon, unknowns[2] if lon else 0.0)
    wheels = car.wheel_states(s, side)
    domain = tuple(flag for w in wheels for flag in w["flags"])
    if residual >= RESIDUAL_TOL:
        # A stationary point outside the tire fit is no evidence that the in-fit car saturates.
        if domain:
            return TrimRecord(TrimStatus.DOMAIN_REJECTED, domain, residual, 1, unknowns, active, wheels)
        return TrimRecord(TrimStatus.PHYSICALLY_INFEASIBLE, ("no_equilibrium",), residual, 1, unknowns, active, wheels)
    if domain:
        return TrimRecord(TrimStatus.DOMAIN_REJECTED, domain, residual, 1, unknowns, active, wheels)
    used = tuple(f"tire_use:{w['corner'].upper()}" for w in wheels if w["use"] >= 1.0)
    if used:
        return TrimRecord(TrimStatus.PHYSICALLY_INFEASIBLE, used, residual, 1, unknowns, active, wheels)
    past = past_peak_axles(car, s, side)
    if past:
        return TrimRecord(TrimStatus.PHYSICALLY_INFEASIBLE, tuple(f"past_peak_axle:{a}" for a in past), residual, 1, unknowns, active, wheels)
    return TrimRecord(TrimStatus.ACCEPTED, (), residual, 1, unknowns, active, wheels)


def trim(car, curve, radius, side, ay_g, outer_max, guess=None, lon=None, max_nfev=None):
    if lon is not None and lon.withheld:
        return TrimRecord(TrimStatus.NOT_MODELED, ("braking_diff_coupling_withheld",))
    outer_lo, outer_hi = max(0.0, curve.domain[0]), min(outer_max, curve.domain[1])
    if outer_lo >= outer_hi:
        return TrimRecord(TrimStatus.DOMAIN_REJECTED, ("steer_domain",))
    rear_path = car.wheelbase * car.front_frac / radius
    outer_path = math.atan(car.wheelbase / (radius + car.track_front / 2))
    lower, upper, extra = [-0.4, outer_lo], [0.4, outer_hi], ()
    if lon:
        lower, upper, extra = lower + [0.0], upper + [3.0 * car.mass * G], (car.mass * G * max(abs(lon.decel_g), 0.05),)
    guesses = [tuple(np.clip(guess, lower, upper))] if guess is not None else [
        (rear_path - db, min(max(outer_path + ds, outer_lo), outer_hi)) + extra
        for db in (0.0, 0.03, 0.06) for ds in (0.0, 0.05, -0.05, 0.10)
    ]
    records = []
    for start in guesses:
        try:
            res = least_squares(
                lambda z: balance(car, curve, radius, side, z, ay_g, lon),
                start, bounds=(lower, upper), xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=max_nfev,
            )
        except SteeringDomainError:
            records.append(TrimRecord(TrimStatus.DOMAIN_REJECTED, ("steer_domain",), None, 1))
            continue
        records.append(classify(car, curve, radius, side, ay_g, lon, res))
        if records[-1].accepted:
            break
    return combine(records)


def limit_ay(car, curve, radius, side, outer_max, lon=None, low=0.1, high=2.5, tol=2e-5):
    if lon is not None and lon.withheld:
        return LimitResult.not_modeled("braking_diff_coupling_withheld")

    def trial(ay_g, warm):
        record = trim(car, curve, radius, side, ay_g, outer_max, None if warm is None else warm.unknowns, lon)
        if warm is not None and not record.accepted:
            record = combine([record, trim(car, curve, radius, side, ay_g, outer_max, lon=lon)])
        return record

    return bisect_limit(trial, (0.02, low, 0.3, 0.6, 1.0), high, tol)


def probe_gain(car, curve, radius, side, outer_max, ay_g, lon=None):
    limit = limit_ay(car, curve, radius, side, outer_max, lon)
    return limit.ay_g / ay_g - 1.0 if limit.is_grip_limit else None


def exit_accel_g(car, max_drive_force):
    def surplus(ax_g):
        fz = car.mass * G * (1.0 - car.front_frac + ax_g * car.cg_height / car.wheelbase) / 2
        return 2.0 * car.peak(car.fx_max_n[1], 2, fz) - car.mass * G * ax_g

    traction = brentq(surplus, 0.05, 3.0, xtol=1e-6)
    return min(traction, max_drive_force / (car.mass * G))


def build_cases(inputs, ggv, tir, curves, outer_max, biases, orion, steer_targets, nominal_car, geometry):
    cases = []
    support = nominal_car.support
    for lky_name, lky in inputs.lky_cases.items():
        tire = {**tir, "LMUY": inputs.mu, "LMUX": inputs.mu, "LKY": lky}
        for lltd_offset in inputs.lltd_offsets:
            car = Car(ggv, (tire,) * 4, ggv.lltd + lltd_offset, support, inputs.static_camber_deg)
            for side_name, side in inputs.slip_sides.items():
                for radius in RADII_M:
                    cases.append({
                        "kind": "main", "car": car, "curves": curves, "outer_max": outer_max,
                        "radius": radius, "side": side, "policy": inputs.policy,
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
            for brake_g in inputs.brake_g:
                for radius in inputs.brake_radii_m:
                    cases.append({
                        "kind": "braking", "car": car, "curves": brake_curves, "outer_max": outer_max,
                        "radius": radius, "lon": Longitudinal(brake_g, bias=bias), "policy": inputs.policy,
                        "labels": {"combined_slip": model, "brake_bias_front": bias, "brake_g": brake_g, "radius_m": radius},
                    })
    toe_curves = {BASELINE: curves[BASELINE]}
    toe_curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase, outer_max) for p in inputs.toe_ackermann_pct})
    for toe in inputs.toe_out_deg:
        for radius in TOE_RADII_M:
            cases.append({
                "kind": "toe", "car": nominal_car, "curves": {k: ToeCurve(v, toe) for k, v in toe_curves.items()},
                "outer_max": outer_max, "radius": radius, "lon": None, "policy": inputs.policy,
                "labels": {"toe_out_deg": toe, "radius_m": radius},
            })
    mass_curves = {name: curves[name] for name in (BASELINE, *MASS_CURVES)}
    tires = nominal_car.tires
    for name, field, delta in inputs.mass_cases:
        variant = types.SimpleNamespace(**{f: getattr(ggv, f) for f in GGV_FIELDS})
        if field == "camber":
            car = Car(variant, tires, nominal_car.lltd, support, inputs.camber_variant_deg)
        else:
            setattr(variant, field, getattr(variant, field) + delta)
            car = Car(variant, tires, nominal_car.lltd, support, inputs.static_camber_deg)
        for radius in TOE_RADII_M:
            cases.append({
                "kind": "mass", "car": car, "curves": mass_curves, "outer_max": outer_max, "radius": radius,
                "lon": None, "policy": inputs.policy, "labels": {"variant": name, "radius_m": radius},
            })
    cases.append({"kind": "track"})
    for target, target_deg in steer_targets.items():
        cases.append({
            "kind": "fix", "vehicle": orion, "target_deg": target_deg, "target_pct": target,
            "track": ggv.track_front, "wheelbase": ggv.wheelbase, "geometry": geometry, "rack_travel_mm": inputs.rack_travel_mm,
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
    return steering_fix(case["vehicle"], case["target_deg"], case["target_pct"], case["track"], case["wheelbase"],
                        case["geometry"], case["rack_travel_mm"])


def wheel_columns(record):
    out = {}
    for w in record.wheels:
        c = w["corner"]
        out.update({
            f"fz_{c}_n": w["fz_n"], f"alpha_{c}_deg": math.degrees(w["alpha_rad"]), f"kappa_{c}": w["kappa"],
            f"gamma_{c}_deg": math.degrees(w["gamma_rad"]), f"use_{c}": w["use"], f"support_{c}": ";".join(w["flags"]),
        })
    return out


def limit_row(labels, limit, ref_ay, policy, lon=None):
    grip = limit.grip_g()
    row = {**labels, **limit.columns(), "ay_max_g": grip, "ay_change_pct": gain_pct(grip, ref_ay, policy)}
    if lon is not None:
        row["driveline_coupling"] = lon.coupling
    if limit.record is not None:
        row["active_bounds"] = ";".join(limit.record.active_bounds)
        row.update(wheel_columns(limit.record))
    return row


def solve_main_case(case):
    car, curves, outer_max, radius, side = case["car"], case["curves"], case["outer_max"], case["radius"], case["side"]
    rows, ref_ay = [], None
    for name, curve in sorted(curves.items(), key=lambda item: item[0] != BASELINE):
        limit = limit_ay(car, curve, radius, side, outer_max)
        if name == BASELINE:
            ref_ay = limit.grip_g()
        row = limit_row({**case["labels"], "curve": name}, limit, ref_ay, case["policy"])
        if limit.ay_g is not None:
            beta, outer = limit.record.unknowns
            s = state(car, curve, radius, side, beta, outer, limit.ay_g)
            inner = s["steer"][0]
            row.update({
                "speed_mps": s["speed"], "beta_deg": math.degrees(beta),
                "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
                "ackermann_pct_at_limit": float(ackermann_pct(inner, outer, car.track_front, car.wheelbase)),
            })
        if case["probe"]:
            front_gain = rear_gain = None
            if limit.is_grip_limit:
                front_gain = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, side, outer_max, limit.ay_g)
                rear_gain = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, side, outer_max, limit.ay_g)
            row.update({
                "ay_gain_pct_per_1pct_front_grip": None if front_gain is None else 100.0 * front_gain,
                "ay_gain_pct_per_1pct_rear_grip": None if rear_gain is None else 100.0 * rear_gain,
                "limiting_axle": limiting_axle(front_gain, rear_gain),
            })
        rows.append(row)
    return rows


def solve_braking_case(case):
    car, curves, outer_max, radius, lon = case["car"], case["curves"], case["outer_max"], case["radius"], case["lon"]
    rows, ref_ay = [], None
    for name, curve in sorted(curves.items(), key=lambda item: item[0] != BASELINE):
        limit = limit_ay(car, curve, radius, 1.0, outer_max, lon)
        if name == BASELINE:
            ref_ay = limit.grip_g()
        row = limit_row({**case["labels"], "curve": name}, limit, ref_ay, case["policy"], lon)
        if limit.ay_g is not None:
            z = limit.record.unknowns
            s = state(car, curve, radius, 1.0, z[0], z[1], limit.ay_g, lon, z[2] if lon else 0.0)
            row.update({
                "beta_deg": math.degrees(z[0]),
                "inner_deg": math.degrees(s["steer"][0]), "outer_deg": math.degrees(z[1]),
                **{f"brake_use_{c}": u for c, u in zip(CORNERS, s["brake_use"])},
                "ackermann_pct_at_limit": float(ackermann_pct(s["steer"][0], z[1], car.track_front, car.wheelbase)),
                **{f"fx_{c}_n": f for c, f in zip(CORNERS, s["wheel_fx"])},
                "longitudinal_n": z[2] if lon else 0.0,
                "max_wheel_use": max(s["brake_use"]),
            })
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


def require_grip_limit(limit, what):
    if not limit.is_grip_limit:
        detail = limit.upper_reason or limit.lower_status.value
        raise RuntimeError(f"{what}: no grip limit ({limit.bound_kind.value}: {detail}). A failed trim is not a limit.")
    return limit


def balanced_lltd(ggv, tire, curve, outer_max, support, camber_deg, radius):
    car = Car(ggv, (tire,) * 4, 0.5, support, camber_deg)

    def negative_ay(lltd):
        car.lltd = lltd
        limit = limit_ay(car, curve, radius, 1.0, outer_max)
        return -require_grip_limit(limit, f"LLTD balance at {lltd:.4f}").ay_g

    return minimize_scalar(negative_ay, bounds=(0.2, 0.8), method="bounded", options={"xatol": 1e-3}).x


def needed_mean_steer_deg(car, curve, radius, outer_max):
    limit = limit_ay(car, curve, radius, 1.0, outer_max)
    if not limit.is_grip_limit:
        return None, f"{limit.bound_kind.value}: {limit.upper_reason or limit.lower_status.value}"
    outer = limit.record.unknowns[1]
    return math.degrees(0.5 * (curve(outer) + outer)), None


def lock_check(table, car, curve, outer_max, tire_width_m, rack_travel_mm):
    # The curve is the full kin_py table, so a need past the rack stop still has a value.
    needed = {r: needed_mean_steer_deg(car, curve, r, outer_max)[0] for r in RADII_M}
    radii = [r for r, n in needed.items() if n is not None]
    need = [needed[r] for r in radii]
    mean = np.degrees(0.5 * (table[:, 1] + table[:, 2]))
    reach = float(np.interp(rack_travel_mm, table[:, 0], mean))
    outer_full = float(np.interp(rack_travel_mm, table[:, 0], table[:, 2]))

    def outer_excess(radius):
        record = trim(car, curve, radius, 1.0, 0.02, outer_max)
        if record.accepted:
            return record.unknowns[1] - outer_full
        if set(record.reasons) == {"steer_bound"}:
            return outer_max - outer_full
        raise RuntimeError(f"Low-speed lock at {radius:g} m: {record.status.value} ({record.reason}).")

    edge = HAIRPIN_OUTSIDE_DIAMETER_M / 2 - car.track_front / 2 - tire_width_m / 2
    return {
        "rack_travel_mm": rack_travel_mm,
        "mean_steer_at_travel_deg": round(reach, 1),
        "outer_at_travel_deg": round(math.degrees(outer_full), 2),
        "min_cg_radius_low_speed_m": round(brentq(outer_excess, 2.5, 12.0, xtol=1e-3), 2),
        "rule_hairpin_cg_radius_m": round(math.sqrt(edge ** 2 - (car.wheelbase * (1.0 - car.front_frac)) ** 2), 2),
        "mean_steer_needed_at_limit_deg": {f"{r:g}m": round(n, 1) for r, n in zip(radii, need)},
        "grip_limited_radii_m": radii,
        "tightest_cg_radius_at_limit_m": round(float(np.interp(-reach, [-n for n in need], radii)), 2) if need else None,
        "rack_needed_for_3p5m_at_limit_mm": (round(float(np.interp(need[0], mean, table[:, 0])), 1)
                                             if radii and radii[0] == RADII_M[0] else None),
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


def steering_fix(vehicle, target_deg, target_pct, track, wheelbase, geometry, rack_travel_mm):
    fix_rack = STEER_RESERVE * rack_travel_mm
    rack = np.append(np.arange(0.0, fix_rack, 0.5), fix_rack)

    def build(d):
        return with_front(vehicle, geometry, d[1] / 1000.0, d[0] / 1000.0, d[2] / 1000.0, d[3] / 1000.0)

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
    solved = bool(solved and len(table) > 0 and table[-1, 0] >= rack_travel_mm)
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
        result["inner_wheel_at_travel"] = lock_geometry(fixed, rack_travel_mm)
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


def brake_model(bias):
    return f"brakes {bias:.2f} open axle"


def pct_label(pct):
    return "n/a" if pct is None else f"{pct:+.0f}%".replace("+0%", "0%")


def ax_label(ax_g):
    return f"{-ax_g:g} g braking" if ax_g < 0 else (f"{ax_g:g} g drive" if ax_g > 0 else "steady")


def yaw_derivatives(car, curve, radius, outer_max, ay_g, lon, step=math.radians(0.05)):
    record = trim(car, curve, radius, 1.0, ay_g, outer_max, lon=lon)
    if not record.accepted:
        return {"deriv_status": record.status.value, "deriv_reason": record.reason}
    z = record.unknowns
    beta, outer, lon_n = z[0], z[1], (z[2] if lon else 0.0)
    norm = car.mass * G * car.wheelbase

    def mz(b, o):
        return state(car, curve, radius, 1.0, b, o, ay_g, lon, lon_n)["mz"] / norm

    mean = lambda o: 0.5 * (curve(o) + o)
    try:
        n_beta = (mz(beta + step, outer) - mz(beta - step, outer)) / (2.0 * step)
        n_delta = (mz(beta, outer + step) - mz(beta, outer - step)) / (mean(outer + step) - mean(outer - step))
    except SteeringDomainError:
        return {"deriv_status": TrimStatus.DOMAIN_REJECTED.value, "deriv_reason": "steer_domain"}
    return {
        "deriv_status": TrimStatus.ACCEPTED.value, "deriv_ay_g": ay_g, "n_beta_per_deg": math.radians(1.0) * n_beta,
        "n_delta_per_deg": math.radians(1.0) * n_delta, "deriv_mean_steer_deg": math.degrees(mean(outer)),
    }


def solve_map_case(case):
    car, curves, radius, lon, outer_max = case["car"], case["curves"], case["radius"], case["lon"], case["outer_max"]
    rows, ref = [], None
    for name, curve in curves.items():
        limit = limit_ay(car, curve, radius, 1.0, outer_max, lon, tol=case["tol"])
        if name == BASELINE:
            ref = limit.grip_g()
        row = limit_row({**case["labels"], "curve": name}, limit, ref, case["policy"], lon)
        if limit.ay_g is not None:
            outer = limit.record.unknowns[1]
            row["inner_deg"], row["outer_deg"] = math.degrees(curve(outer)), math.degrees(outer)
            row["lock_margin_deg"] = math.degrees(min(outer_max, curve.domain[1]) - outer)
        if limit.is_grip_limit:
            if case["probe"] and name == BASELINE:
                front = probe_gain(car.with_grip(front=GRIP_PROBE), curve, radius, 1.0, outer_max, limit.ay_g, lon)
                rear = probe_gain(car.with_grip(rear=GRIP_PROBE), curve, radius, 1.0, outer_max, limit.ay_g, lon)
                row["limiting_axle"] = limiting_axle(front, rear)
            if case["deriv"]:
                row.update(yaw_derivatives(car, curve, radius, outer_max, DERIV_FRACTION * limit.ay_g, lon))
        rows.append(row)
    return rows


def cap_allowed(row):
    return row["ay_accepted_g"] is None and row["trim_status"] == TrimStatus.PHYSICALLY_INFEASIBLE.value


def check_map(rows, curve, brake_g, drive_g, inputs):
    grid = {(r["radius_m"], r["ax_g"]): r for r in rows if r["curve"] == curve}
    caps, excluded = [], []
    for radius in inputs.map_radii_m:
        if grid[(radius, 0.0)]["ay_max_g"] is None:
            excluded.append({"curve": curve, "radius_m": radius, "ax_g": 0.0, "bound_kind": grid[(radius, 0.0)]["bound_kind"]})
            continue
        for sign, endpoint in ((-1.0, brake_g), (1.0, drive_g)):
            levels = sorted((x for x in inputs.map_ax_g if x * sign > 0), key=abs)
            found = [grid[(radius, x)]["ay_max_g"] is not None for x in levels]
            if all(found):
                continue
            first = found.index(False)
            row = grid[(radius, levels[first])]
            if not cap_allowed(row):
                excluded.append({"curve": curve, "radius_m": radius, "ax_g": levels[first], "bound_kind": row["bound_kind"]})
                continue
            if any(found[first:]):
                raise RuntimeError(f"{curve} has an envelope gap at {radius} m")
            caps.append({"curve": curve, "radius_m": radius, "side": "drive" if sign > 0 else "brake",
                         "cap_g": abs(levels[first - 1]) if first > 0 else 0.0, "endpoint_g": round(endpoint, 3)})
    return caps, excluded


def envelope_ggv(rows, curve, brake_g, drive_g, mass, power_w, inputs, refine=False):
    radii = np.asarray(inputs.map_radii_m)
    # NaN marks a missing cell inside this function only. check_map admits only physical caps.
    grid = {(r["radius_m"], r["ax_g"]): (math.nan if r["ay_max_g"] is None else r["ay_max_g"]) for r in rows if r["curve"] == curve}
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
        if r["mu"] == inputs.mu and r["ay_max_g"] is not None:
            top[(r["radius_m"], r["ax_g"])] = max(top.get((r["radius_m"], r["ax_g"]), 0.0), r["ay_max_g"])
    rows = [{**r, "ay_max_g": top.get((r["radius_m"], r["ax_g"]))} if cap_allowed(r) else r
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
    count = 400
    angle = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    step = 2.0 * math.pi * CIRCLE_CHECK_RADIUS_M / count
    line = RacingLine(
        station_m=np.arange(count) * step, x_m=CIRCLE_CHECK_RADIUS_M * np.cos(angle), y_m=CIRCLE_CHECK_RADIUS_M * np.sin(angle),
        heading_rad=angle + math.pi / 2.0, curvature_per_m=np.full(count, 1.0 / CIRCLE_CHECK_RADIUS_M),
        segment_length_m=np.full(count, step), gate_offsets_m=np.zeros(count), track_length_m=count * step,
    )
    lap = solve_qss_lap(line, envelope_ggv(rows, BASELINE, brake_g, drive_g, mass, power_w, inputs))
    ay = next(r["ay_max_g"] for r in rows if r["curve"] == BASELINE and r["ax_g"] == 0.0 and r["radius_m"] == CIRCLE_CHECK_RADIUS_M)
    expected = 2.0 * math.pi * CIRCLE_CHECK_RADIUS_M / math.sqrt(ay * G * CIRCLE_CHECK_RADIUS_M)
    error = lap.lap_time_s / expected - 1.0
    if abs(error) > CIRCLE_CHECK_TOL:
        raise RuntimeError(f"QSS circle check failed: {lap.lap_time_s:.4f} s against {expected:.4f} s")
    return {"radius_m": CIRCLE_CHECK_RADIUS_M, "qss_s": round(lap.lap_time_s, 4), "hand_s": round(expected, 4),
            "error_pct": round(100.0 * error, 3)}


def condition_label(row):
    car = row.get("variant") or f"grip {row['mu']:g}"
    return f"{car}, {row['rear']}, {row['radius_m']:g} m, {ax_label(row['ax_g'])}"


def observation(row, condition):
    reason = row.get("upper_reason") or row.get("trim_status", "")
    return Observation(condition, row["curve"], row["ay_max_g"], row["bound_kind"], reason)


def robust_region(rows, policy):
    laws = tuple(dict.fromkeys(r["curve"] for r in rows))
    result = compare([observation(r, condition_label(r)) for r in rows], laws, BASELINE, policy)
    return {
        "status": result.status, "reason": result.reason, "n_supported": result.n_supported, "n_total": result.n_total,
        "coverage": round(result.coverage, 4), "floor_pct": result.floor_pct,
        "region": {k: list(v) for k, v in result.region.items()},
        "excluded_by_kind": result.excluded_by_kind(),
        "excluded": [dataclasses.asdict(e) for e in result.excluded],
        "laws": {law: dataclasses.asdict(v) for law, v in result.laws.items()},
    }


def region_text(robust):
    if robust["status"] != "ok":
        return f"No recommendation (withheld: {robust['reason']}, {robust['n_supported']} of {robust['n_total']} conditions supported)"
    return ", ".join(next(iter(robust["region"].values())))


def hairpin_time_s(rows, curve, radius):
    ay = next(r["ay_max_g"] for r in rows if r["curve"] == curve and r["ax_g"] == 0.0 and r["radius_m"] == radius)
    return None if ay is None else math.pi * radius / math.sqrt(ay * G * radius)


def best_laws(op_rows, inputs):
    cells = {}
    for r in op_rows:
        if r["mu"] == inputs.mu:
            cells.setdefault((r["radius_m"], r["ax_g"]), {})[r["curve"]] = observation(r, "")
    laws = {f"{p:+d}%": p for p in MAP_PCTS}
    out = {}
    for radius in inputs.map_radii_m:
        for ax in inputs.map_ax_g:
            best = cell_best(cells[(radius, ax)], tuple(laws), BASELINE, inputs.policy, BEST_MIN_GAIN_PCT)
            if best.law is None:
                out[(radius, ax)] = (None, None)
            else:
                out[(radius, ax)] = (laws.get(best.law, 0), best.gain_pct)
    return out


def best_text(pct, gain, sep=" "):
    if pct is None:
        return "withheld"
    if pct == 0:
        return "parallel"
    return f"{pct_label(pct)}{sep}({signed(gain)})"


def coverage_case(case):
    car, curve, outer_max, bias = case["car"], case["curve"], case["outer_max"], case["bias"]
    out = []
    for radius, ax_g, ay_g, dt, phase in case["points"]:
        ax, ay = COVERAGE_DEMAND * ax_g, COVERAGE_DEMAND * ay_g
        if phase == "braking":
            lon = Longitudinal(-ax, bias=bias)
        else:
            lon = Longitudinal(-ax, diff=case["drive_diff"], wheel_radius=case["rear_radius"])
        record = trim(car, curve, radius, 1.0, ay, outer_max, lon=lon)
        if not record.accepted:
            guess = trim(car, curve, radius, 1.0, 0.5 * ay, outer_max, lon=lon)
            if guess.accepted:
                record = combine([record, trim(car, curve, radius, 1.0, ay, outer_max, guess.unknowns, lon)])
        out.append((phase, dt, record.status.value))
    return out


def trim_coverage(lap, curvature, car, curve, outer_max, drive_diff, rear_radius, bias):
    points, count = [], len(curvature)
    for i in range(0, count, COVERAGE_STEP):
        ax = lap["ax_mps2"][i] / G
        phase = "braking" if ax < -0.05 else ("exit" if ax > 0.05 else "steady")
        j = (i + 1) % count if phase == "braking" else i
        ay = lap["speed_mps"][j] ** 2 * abs(curvature[j]) / G
        radius = 1.0 / max(abs(curvature[j]), 1e-9)
        if ay < 0.05 or radius > CORNER_RADIUS_MAX_M:
            continue
        points.append((radius, ax, ay, lap["segment_time_s"][i], phase))
    chunks = [points[k::24] for k in range(24)]
    cases = [{"kind": "coverage", "car": car, "curve": curve, "outer_max": outer_max, "drive_diff": drive_diff,
              "rear_radius": rear_radius, "bias": bias, "points": chunk} for chunk in chunks if chunk]
    results = [r for chunk in map_cases(solve_case, cases) for r in chunk]
    out = {"points": len(points), "demand": COVERAGE_DEMAND, "radius_max_m": CORNER_RADIUS_MAX_M}
    accepted = TrimStatus.ACCEPTED.value
    for phase in ("braking", "steady", "exit", "all"):
        sel = [(dt, status) for p, dt, status in results if phase in (p, "all")]
        out[phase] = round(sum(dt for dt, status in sel if status == accepted) / max(sum(dt for dt, _ in sel), 1e-9), 3)
    out["points_by_status"] = {s: sum(1 for _, _, status in results if status == s) for s in sorted({r[2] for r in results})}
    return out


def clipped_braking(op_rows):
    steady = {(r["mu"], r["radius_m"], r["curve"]): r["ay_max_g"] for r in op_rows if r["ax_g"] == 0.0}
    pairs = [(r, steady[(r["mu"], r["radius_m"], r["curve"])]) for r in op_rows if r["ax_g"] < 0.0]
    excess = [(r["ay_max_g"] - s, r) for r, s in pairs if r["ay_max_g"] is not None and s is not None]
    clipped = [(e, r) for e, r in excess if e > 0.0]
    top = max(clipped, key=lambda item: item[0]) if clipped else (0.0, {})
    return {
        "cells": len(clipped), "of": len(excess), "unavailable": len(pairs) - len(excess), "max_g": round(top[0], 3),
        "max_at": {k: top[1].get(k) for k in ("curve", "radius_m", "ax_g", "mu")},
        "by_curve": {c: round(max([e for e, r in clipped if r["curve"] == c], default=0.0), 3)
                     for c in dict.fromkeys(r["curve"] for r in op_rows)},
    }


def skidpad_time_s(rows, curve, inputs):
    steady = {r["radius_m"]: r["ay_max_g"] for r in rows if r["curve"] == curve and r["ax_g"] == 0.0}
    ay = float(np.interp(SKIDPAD_RADIUS_M, inputs.map_radii_m, [steady[r] for r in inputs.map_radii_m]))
    return 2.0 * math.pi * SKIDPAD_RADIUS_M / math.sqrt(ay * G * SKIDPAD_RADIUS_M)


def lap_phases(laps, curvature, op_rows, inputs):
    radius = 1.0 / np.maximum(np.abs(curvature), 1e-9)
    bands = np.digitize(radius, LAP_RADIUS_BANDS_M)
    band_names = [f"under {LAP_RADIUS_BANDS_M[0]:g} m", f"{LAP_RADIUS_BANDS_M[0]:g} to {LAP_RADIUS_BANDS_M[1]:g} m",
                  f"over {LAP_RADIUS_BANDS_M[1]:g} m"]
    out = {}
    for mu in sorted({lap["mu"] for lap in laps}):
        base = next((lap for lap in laps if lap["mu"] == mu and lap["curve"] == BASELINE), None)
        if base is None:
            out[f"{mu:g}"] = {}
            continue
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


def optimum_check(car, rows):
    out = {}
    for radius in RADII_M[:4]:
        row = next(r for r in rows if is_nominal(r) and r["radius_m"] == radius and r["curve"] == "+75%")
        if row["ay_max_g"] is None:
            out[f"{radius:g}m"] = {"status": "unavailable", "reason": f"+75% has no grip limit ({row['bound_kind']})"}
            continue
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
    if front_gain is None or rear_gain is None:
        return None
    return "front" if front_gain > rear_gain else "rear"


def max_change_gap(rows, ref_rows, match, curves):
    ref = {tuple(r[k] for k in match): r["ay_change_pct"] for r in ref_rows}
    pairs = [(r["ay_change_pct"], ref.get(tuple(r[k] for k in match))) for r in rows if r["curve"] in curves]
    gaps = [abs(a - b) for a, b in pairs if a is not None and b is not None]
    return {"value": max(gaps) if gaps else None, "n_valid": len(gaps), "n_total": len(pairs)}


def toe_summary(rows, policy):
    ref = {r["radius_m"]: r["ay_max_g"] for r in rows if r["toe_out_deg"] == 0.0 and r["curve"] == BASELINE}
    out = {}
    for r in rows:
        key = f"toe-out {r['toe_out_deg']:g} deg, {r['radius_m']:g} m"
        entry = out.setdefault(key, {"change_vs_base_zero_toe_pct": {}})
        change = gain_pct(r["ay_max_g"], ref.get(r["radius_m"]), policy)
        entry["change_vs_base_zero_toe_pct"][r["curve"]] = None if change is None else round(change, 2)
    for entry in out.values():
        changes = {k: v for k, v in entry["change_vs_base_zero_toe_pct"].items() if k != BASELINE}
        missing = sorted(k for k, v in changes.items() if v is None)
        entry["best_curve"] = None if missing or not changes else max(changes, key=changes.get)
        entry["excluded"] = missing
    return out


def signed(x, unit="%", digits=1):
    if x is None or not math.isfinite(x):
        return "n/a"
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


def dig(value, *keys):
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def difference(a, b, scale=1.0):
    return None if a is None or b is None else scale * (a - b)


def apex_cell(value, spread):
    lo, hi, n_valid, n_total = spread
    count = "" if n_valid == n_total else f", {n_valid} of {n_total} cases"
    return f"{signed(value)} ({span(lo, hi)}{count})" if n_valid else f"{signed(value)} (no valid case)"


def robust_md(robust, beyond):
    yes = {True: "yes", False: "no"}
    laws, tols = robust["laws"], list(robust["region"])
    order = sorted(laws, key=lambda c: (laws[c]["worst_shortfall_pct"] is None, laws[c]["worst_shortfall_pct"] or 0.0))
    text = table(["Option", "Worst shortfall", "Binding condition", *(f"In region, {t} pt" for t in tols),
                  "Worst beyond 3.5 m", f"In region beyond 3.5 m, {tols[0]} pt"], [
        [c, signed(laws[c]["worst_shortfall_pct"], "%", 1).lstrip("+"), laws[c]["binding"] or "n/a",
         *(yes[laws[c]["in_region"][t]] for t in tols),
         signed(beyond["laws"][c]["worst_shortfall_pct"], "%", 1).lstrip("+"), yes[beyond["laws"][c]["in_region"][tols[0]]]]
        for c in order
    ])
    lines = []
    for name, r in (("All conditions", robust), ("Beyond 3.5 m", beyond)):
        kinds = ", ".join(f"{k} {n}" for k, n in r["excluded_by_kind"].items()) or "none"
        status = "ok" if r["status"] == "ok" else f"withheld ({r['reason']})"
        lines.append(f"{name}: {r['n_supported']} of {r['n_total']} conditions have common support. "
                     f"Excluded law cells by kind: {kinds}. Status: {status}.")
    return text + "\n" + "\n\n".join(lines) + "\n"


def findings_md(robust, beyond):
    if robust["status"] != "ok":
        return (f"- **No recommendation.** The comparison is withheld ({robust['reason']}). "
                f"{robust['n_supported']} of {robust['n_total']} conditions have common support. "
                "The findings of an earlier run do not apply.\n")
    without = region_text(beyond) if beyond["status"] == "ok" else "withheld"
    return (f"- **Robust region.** {region_text(robust)} at {next(iter(robust['region']))} point tolerance. Without the 3.5 m cells: {without}. "
            "A person writes the findings of this run from the tables on this page.\n")


WITHHELD_REASONS = {
    "robust_floor_pct": "the comparison is withheld",
    "convergence_band_ms": "no refined lap",
    "fix_rack_mm": "no linkage was solved",
    "brake_model_gap_pts": "no pair of valid rows",
    "mass_gap_pts": "no pair of valid rows",
    "clip_max_curve": "no clipped cell",
    **{f"{law}_lap{sens}_s": "the law has no lap reference" for law in ("anti50", "pro50", "design") for sens in ("", "_sensitivity")},
}


def shown(values):
    return {k: f"withheld: {WITHHELD_REASONS.get(k, 'no valid value in this run')}" if v is None else v
            for k, v in finite(values).items()}


def write_readme_parts(out, s, op_rows, inputs):
    nominal, spread, fix = s["nominal"], s["ay_change_pct_range_all_cases"], s["steering_fix"]
    laws = [f"{p:+d}%" for p in MAP_PCTS]
    parts = {}
    parts["apex"] = table(["R", *laws], [
        [f"{r:g} m"] + [apex_cell(nominal[f'{r:g}m'][c]['ay_change_pct'], spread[f'{r:g}m'][c]) for c in laws]
        for r in RADII_M
    ])

    front = s["front_base_linkage"]
    plane = front["tie_o_inboard_of_wheel_center_plane_mm"]
    link = [linkage_row(DESIGN, None, front["arm_offset_mm"], plane, front["inner_wheel_at_travel"])]
    for f in fix:
        link.append(linkage_row(pct_label(f["target_ackermann_pct"]), f, f["arm_offset_mm"],
                                f["tie_o_inboard_of_wheel_center_plane_mm"], f.get("inner_wheel_at_travel")))
    for name, why in s.get("steering_fix_skipped", {}).items():
        link.append([name, f"not solved: the 3.5 m limit is not a grip limit ({why})", "–", "–", "–", "–", "–"])
    parts["linkage"] = table(["Option", "Tie rod outer move", "To wheel center plane", "Arm to kingpin", "Rack pickup move",
                              "Toggle margin at inner lock", "Bump toe at inner lock, ±25 mm"], link)

    optimum = s["first_principles_optimum"]
    parts["optimum"] = table(["R", "Front-limited optimum", "Solver optimum (10% steps)", "+75% limited by"], [
        [f"{r:g} m", pct_label(dig(optimum, f"{r:g}m", "yaw_weighted_optimum_pct")),
         s["toe"][f"toe-out 0 deg, {r:g} m"]["best_curve"] or "withheld",
         nominal[f"{r:g}m"]["+75%"].get("limiting_axle") or "n/a"] for r in TOE_RADII_M
    ])

    limits = s["team_2027_inputs"]["straight_braking_limit"]
    parts["bias"] = table(["Front bias", "Locks first", "Straight-line limit"], [
        [f"{100 * float(b):.0f}%", v["locks_first"], f"{v['limit_g']:.2f} g"] for b, v in limits.items()
    ])

    parts["toe"] = table(["Total toe-out", "3.5 m best", "4.5 m best"], [
        [signed(t, "°") + (" (toe-in)" if t < 0 and t == min(inputs.toe_out_deg) else "")]
        + [s["toe"][f"toe-out {t:g} deg, {r:g} m"]["best_curve"] or "withheld" for r in TOE_RADII_M] for t in inputs.toe_out_deg
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

    def deriv(r):
        if r.get("deriv_status") != TrimStatus.ACCEPTED.value:
            return "n/a"
        return f"{1000 * r['n_beta_per_deg']:+.1f} / {1000 * r['n_delta_per_deg']:.1f}".replace("-", "−")

    parts["limit"] = table(["R", *LIMIT_CURVES], [
        [f"{radius:g} m"] + [deriv(cell[(inputs.mu, radius, 0.0, c)]) for c in LIMIT_CURVES] for radius in inputs.deriv_radii_m
    ])

    others = [c for c in s["operating_map"]["curves"] if c != BASELINE]
    nominal_rows = [r for r in op_rows if r["mu"] == inputs.mu]
    parts["hairpin"] = table(["Option", *(f"{r:g} m" for r in TOE_RADII_M)], [
        [c] + [signed(difference(hairpin_time_s(nominal_rows, c, r), hairpin_time_s(nominal_rows, BASELINE, r), 1000.0), " ms", 0)
               for r in TOE_RADII_M] for c in others
    ])

    lock_rows = []
    for c in s["operating_map"]["curves"]:
        r = cell[(inputs.mu, 3.5, 0.0, c)]
        if r["ay_max_g"] is None:
            lock_rows.append([c, f"n/a ({r['bound_kind']})", "n/a"])
            continue
        mean = 0.5 * (r["inner_deg"] + r["outer_deg"])
        lock_rows.append([c, f"{mean:.1f}°", f"{r['inner_deg']:.1f}° / {r['outer_deg']:.1f}°"])
    parts["lock"] = table(["Option", "Mean steer at the 3.5 m limit", "Inner / outer"], lock_rows)

    parts["robust"] = robust_md(s["robust"]["all"], s["robust"]["beyond_3p5"])
    parts["findings"] = findings_md(s["robust"]["all"], s["robust"]["beyond_3p5"])

    laps = s["lap_qss"]
    mus = [m for m in laps if m != "radius_bands"]

    def lap_cell(m, c, key, digits):
        if c not in laps[m]:
            return "not modeled"
        return signed(laps[m][c][key], " s", digits)

    parts["lap"] = table(
        ["Option", *(f"Endurance lap, grip {m}" for m in mus), *(f"Skidpad lap, grip {m}" for m in mus)],
        [[c] + [lap_cell(m, c, "delta_s", 2) for m in mus] + [lap_cell(m, c, "skidpad_delta_s", 3) for m in mus] for c in others],
    )

    for name, text in parts.items():
        (out / f"{name}.md").write_text(text, encoding="utf-8")

    lock = s["lock_at_rack_travel"]
    ack = s["ackermann_pct_at_rack"][DESIGN]
    signs = [pct for pct, _ in best.values() if pct is not None]
    band = s["grid_convergence"]["band_s"]
    tied = {m: [c for c in others if c in laps[m] and band is not None and abs(laps[m][c]["delta_s"]) <= band] for m in mus}
    lsd = {(r["radius_m"], r["ax_g"], r["curve"]): r["ay_change_pct"] for r in s["lsd_rows"]}
    cov = s["trim_coverage"]
    track = s["track_minimum_curvature_line"]
    bias = limits[f"{inputs.brake_bias:.2f}"]
    design_3p5 = cell[(inputs.mu, 3.5, 0.0, DESIGN)]
    hard_brake = [best[(3.5, a)][0] for a in sorted(inputs.map_ax_g) if best[(3.5, a)][0] is not None]
    authority = [dig(cell, (inputs.mu, 3.5, 0.0, c), "n_delta_per_deg") for c in ("+50%", BASELINE)]
    margins = [v for v in s["operating_map"]["lock_margin_min_deg"].values() if v is not None]
    values = {
        "lltd_front_pct": 100.0 * s["vehicle"]["lltd_front"],
        "base_ackermann_pct": f"{signed(min(ack.values()))} to {signed(max(ack.values()))}",
        "steer_at_travel_deg": lock["mean_steer_at_travel_deg"],
        "steer_needed_3p5_deg": lock["mean_steer_needed_at_limit_deg"].get("3.5m"),
        "steer_needed_3p5_powered_deg": (0.5 * (design_3p5["inner_deg"] + design_3p5["outer_deg"])
                                         if design_3p5["ay_max_g"] is not None else None),
        "cap_check_max_ms": s["cap_check"]["max_change_ms"],
        "tightest_radius_m": lock["tightest_cg_radius_at_limit_m"],
        "min_radius_low_speed_m": lock["min_cg_radius_low_speed_m"],
        "rule_hairpin_radius_m": lock["rule_hairpin_cg_radius_m"],
        "rear_baseline": REAR_BASELINE,
        "robust_status": s["robust"]["all"]["status"],
        "robust_floor_pct": s["robust"]["all"]["floor_pct"] if s["robust"]["all"]["status"] == "ok" else None,
        "robust_laws": region_text(s["robust"]["all"]), "robust_laws_beyond_3p5": region_text(s["robust"]["beyond_3p5"]),
        "robust_supported": f"{s['robust']['all']['n_supported']} of {s['robust']['all']['n_total']}",
        "lsd_pro50_apex_3p5": lsd.get((3.5, 0.0, "+50%")), "lsd_anti50_apex_3p5": lsd.get((3.5, 0.0, "-50%")),
        "lsd_pro50_drive_3p5": lsd.get((3.5, 0.3, "+50%")),
        "variants": ", ".join(s["operating_map"]["variants"]),
        "rack_needed_mm": lock["rack_needed_for_3p5m_at_limit_mm"],
        "arm_offset_mm": front["arm_offset_mm"],
        "fix_rack_mm": fix[0]["target_rack_mm"] if fix else None,
        "mu_nominal": mus[0], "mu_sensitivity": mus[-1],
        "lap_tied_laws": ", ".join(tied[mus[0]]), "lap_tied_laws_sensitivity": ", ".join(tied[mus[-1]]),
        "lap_not_modeled": ", ".join(sorted({e["curve"] for e in s["operating_map"]["lap_excluded"]})) or "none",
        "convergence_band_ms": None if band is None else 1000.0 * band,
        "base_ackermann_full_travel_pct": ack[f"{inputs.rack_travel_mm:g}mm"],
        "caps_count": len(s["operating_map"]["caps"]),
        "lock_limited_count": len(s["operating_map"]["lock_limited_cells"]),
        "lock_limited_nominal_count": sum(1 for c in s["operating_map"]["lock_limited_cells"] if "variant" not in c),
        "lock_margin_min_deg": min(margins) if margins else None,
        "anti50_lap_s": dig(laps, mus[0], "-50%", "delta_s"), "anti50_lap_sensitivity_s": dig(laps, mus[-1], "-50%", "delta_s"),
        "pro50_lap_s": dig(laps, mus[0], "+50%", "delta_s"), "pro50_lap_sensitivity_s": dig(laps, mus[-1], "+50%", "delta_s"),
        "design_lap_s": dig(laps, mus[0], DESIGN, "delta_s"), "design_lap_sensitivity_s": dig(laps, mus[-1], DESIGN, "delta_s"),
        "map_cells": len(best), "withheld_cells": len(best) - len(signs), "pro_cells": sum(1 for v in signs if v > 0), "anti_cells": sum(1 for v in signs if v < 0),
        "parallel_cells": sum(1 for v in signs if v == 0),
        "best_3p5_steady": pct_label(best[(3.5, 0.0)][0]), "best_3p5_steady_gain_pct": best[(3.5, 0.0)][1],
        "best_3p5_hard_brake": pct_label(hard_brake[0]) if hard_brake else "withheld",
        "pro50_apex_3p5": cell[(inputs.mu, 3.5, 0.0, "+50%")]["ay_change_pct"],
        "anti50_apex_3p5": cell[(inputs.mu, 3.5, 0.0, "-50%")]["ay_change_pct"],
        "pro50_authority_ratio_3p5": authority[0] / authority[1] if None not in authority else None,
        "circle_check_error_pct": s["circle_check"]["error_pct"],
        "coverage_pct": 100.0 * cov["all"], "coverage_points": cov["points"],
        "coverage_demand_pct": 100.0 * cov["demand"], "coverage_radius_max_m": cov["radius_max_m"],
        "clip_cells": s["clipped_braking"]["cells"], "clip_of": s["clipped_braking"]["of"],
        "clip_max_g": s["clipped_braking"]["max_g"], "clip_max_curve": s["clipped_braking"]["max_at"]["curve"],
        "clip_anti50_g": s["clipped_braking"]["by_curve"].get("-50%"), "clip_pro50_g": s["clipped_braking"]["by_curve"].get("+50%"),
        "ideal_bias_pct": 100.0 * bias["ideal_front_bias"],
        "ideal_limit_g": bias["ideal_limit_g"],
        "brake_model_gap_pts": s["brake_model_gap_pts"]["value"],
        "points_per_deg_toe_3p5": dig(s, "first_principles_optimum", "3.5m", "points_per_deg_toe_out"),
        "mass_gap_pts": s["mass_gap_pts"]["value"],
        "track_length_m": track["length_m"],
        "corners_under_15m": track["corners_under_15m"],
        "tightest_corner_m": min(c["min_radius_m"] for c in track["corners"]),
        "tire_sha256": s["tire"]["sha256"][:16],
    }
    (out / "readme.json").write_text(json.dumps(shown(values), indent=2), encoding="utf-8")


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


def status_counts(rows):
    counts = {}
    for r in rows:
        key = r["bound_kind"] if r["ay_accepted_g"] is not None else f"{r['bound_kind']}, no accepted trim"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


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


def plot_grip(path, rows, base_pct, op_rows, inputs):
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.5, 4.4), facecolor=SURFACE)
    pct = np.array(inputs.ackermann_pct, dtype=float)
    plot_best(right, op_rows, inputs)
    for radius, color in zip(RADII_M, RADIUS_COLORS):
        def series(key, select):
            picked = [next(r.get(key) for r in rows if select(r) and r["radius_m"] == radius and r["curve"] == f"{p:+d}%") for p in inputs.ackermann_pct]
            return np.array([np.nan if v is None else v for v in picked], dtype=float)

        nominal = series("ay_change_pct", is_nominal)
        cases = {(r["tire_case"], r["slip_side"], r["lltd_offset"]) for r in rows}
        spread = np.array([series("ay_change_pct", lambda r, c=c: (r["tire_case"], r["slip_side"], r["lltd_offset"]) == c) for c in cases])
        left.fill_between(pct, np.nanmin(spread, axis=0), np.nanmax(spread, axis=0), color=color, alpha=0.15, lw=0)
        left.plot(pct, nominal, color=color, lw=2, marker="o", ms=4, label=f"R = {radius:g} m")
        design_gain = next(r["ay_change_pct"] for r in rows if is_nominal(r) and r["radius_m"] == radius and r["curve"] == DESIGN)
        if design_gain is not None and base_pct.get(radius) is not None:
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


def plot_best(ax, op_rows, inputs):
    best = best_laws(op_rows, inputs)
    values = np.array([[np.nan if best[(r, a)][0] is None else best[(r, a)][0] for r in inputs.map_radii_m] for a in inputs.map_ax_g], dtype=float)
    ax.imshow(values, cmap=GAIN_MAP, norm=TwoSlopeNorm(0.0, min(MAP_PCTS), max(MAP_PCTS)), aspect="auto", origin="lower")
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


def load_inputs(path=INPUTS_FILE, mu=None, bias=None):
    study = load_study_inputs(path, INPUT_SPEC, study="front-ackermann")
    if mu is not None:
        study = study.override("tire.grip_scale", mu, "command line --mu")
    if bias is not None:
        study = study.override("brake.front_bias_nominal", bias, "command line --bias")
    missing = sorted(key for key, field in study.fields.items() if field.value is None)
    if missing:
        raise StudyInputsError(f"{study.path.name}: the run needs a value for {', '.join(missing)}.")
    if study.geometry.label != DESIGN:
        raise StudyInputsError(f"{study.path.name} names the front geometry {study.geometry.label!r}. "
                               f"run.py labels the design curve {DESIGN!r}. Make them the same.")
    if study.value("alignment.static_toe") != 0.0:
        raise StudyInputsError("The corner model has no static toe input. Set alignment.static_toe to 0. The toe sweep varies toe.")
    if study.value("differential.braking_coupling") != "withheld":
        raise StudyInputsError("The study does not model LSD coupling under braking. Set differential.braking_coupling to withheld.")
    return study


def check_alignment(study, vehicle):
    for key, name in (("alignment.static_camber", "camber_deg"), ("alignment.static_toe", "toe_deg")):
        want = study.value(key)
        for axle in ("front", "rear"):
            have = vehicle[axle].get("wheel", {}).get(name)
            if have is None or abs(float(have) - want) > 1e-9:
                raise StudyInputsError(
                    f"{study.path.name} sets {key} to {want:g} deg, but the vehicle file has {axle}.wheel.{name} = {have}. "
                    "The corner model applies one value to every wheel. Make them the same.")


def check_bundle_reference(study, vehicle_path, bundle_path=VEHICLE_BUNDLE):
    want = study.bundle_id.value
    if not Path(bundle_path).is_file():
        raise StudyInputsError(f"{study.path.name} names bundle_id {want!r}, but {bundle_path} does not exist.")
    bundle = load_bundle(bundle_path)
    if want != bundle.bundle_id:
        raise StudyInputsError(
            f"{study.path.name} names bundle_id {want!r}, but {bundle.path.name} is {bundle.bundle_id!r}. "
            "Set bundle_id in inputs.yml to the bundle that describes the vehicle.")
    member, executed = bundle.member_hashes().get("vehicle"), file_sha256(vehicle_path)
    if member != executed:
        raise StudyInputsError(
            f"Bundle {bundle.bundle_id} describes a vehicle file with SHA-256 {member}, but BOBSIM_VEHICLE "
            f"{vehicle_path} has SHA-256 {executed}. Run the vehicle that the bundle describes, or update the bundle.")
    return bundle


def parse_inputs(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=INPUTS_FILE, help="study inputs file, schema lhre.study-inputs.v1")
    parser.add_argument("--mu", type=float, default=None, help="override the tire grip scale, LMUY = LMUX")
    parser.add_argument("--ackermann", type=int, nargs="*", default=[], help="extra Ackermann %% to sweep and solve a linkage for")
    parser.add_argument("--bias", type=float, default=None, help="override the nominal front brake bias, 0 to 1")
    parser.add_argument("--quick", action="store_true", help="small sweep that runs every code path, for a smoke test")
    args = parser.parse_args(argv)
    study = load_inputs(args.inputs, args.mu, args.bias)
    v = study.value
    inputs = Inputs(
        mu=v("tire.grip_scale"), mu_sensitivity=v("tire.grip_scale_sensitivity"), brake_bias=v("brake.front_bias_nominal"),
        rack_travel_mm=v("steering.rack_travel"), static_camber_deg=v("alignment.static_camber"),
        camber_variant_deg=v("alignment.camber_variant"), balance_radius_m=v("lltd.balance_radius"),
        planned_diff=(v("differential.planned_lsd_preload"), v("differential.planned_lsd_lock")),
        mass_properties={key: v(f"mass_properties.{key}") for key in ("mass", "cg_height", "front_static_frac")},
        policy=SupportPolicy(min_coverage=v("policy.min_coverage"), ay_floor_g=v("policy.ay_floor"),
                             tolerances_pct=tuple(v("policy.region_tolerances"))),
        ackermann_pct=tuple(sorted(set(ACKERMANN_PCT) | set(args.ackermann))),
        steer_fix_targets_pct=tuple(sorted(set(STEER_FIX_TARGETS_PCT) | {float(p) for p in args.ackermann})),
    )
    return (dataclasses.replace(inputs, **QUICK) if args.quick else inputs), study


def main():
    inputs, study = parse_inputs()
    out = Path(os.environ["OUT_DIR"])
    vehicle_path = Path(os.environ["BOBSIM_VEHICLE"])
    bundle = check_bundle_reference(study, vehicle_path)
    orion = load_yaml(vehicle_path)
    check_alignment(study, orion)
    front_geometry = study.geometry
    base = with_front(orion, front_geometry)
    (out / "vehicle_front.yml").write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")

    projection = project_vehicle_yaml(base)
    ggv = types.SimpleNamespace(**{f: getattr(projection.ggv, f) for f in GGV_FIELDS})
    for field, value in inputs.mass_properties.items():
        setattr(ggv, field, value)
    orion_ggv = project_vehicle_yaml(orion).ggv
    orion_table, _ = steer_table(orion)
    base_table, _ = steer_table(base)

    write_csv(out / "steering_curves.csv", [
        {"car": name, "rack_mm": rack, "inner_deg": math.degrees(inner), "outer_deg": math.degrees(outer),
         "ackermann_pct": ackermann_pct(inner, outer, g.track_front, g.wheelbase) if rack >= 2.0 else float("nan")}
        for name, t, g in (("Orion", orion_table, orion_ggv), (DESIGN, base_table, ggv)) for rack, inner, outer in t
    ])

    outer_max = float(base_table[-1, 2])
    curves = {BASELINE: ConstantAckermann(0, ggv.track_front, ggv.wheelbase, outer_max)}
    curves.update({f"{p:+d}%": ConstantAckermann(p, ggv.track_front, ggv.wheelbase, outer_max) for p in inputs.ackermann_pct})
    curves[DESIGN] = TableCurve(*to_rack_stop(base_table, inputs.rack_travel_mm))
    tir_path = tire_templates_root(base) / f"{base['front']['tire']['template']}.tir"
    tir = parse_tir(tir_path)
    support = TireSupport.from_tir(tir_path)
    lap_config = repo_root() / LAP_CONFIG
    power_w = float(load_yaml(lap_config)["event"]["drive_power_limit_w"])
    files = {"vehicle": vehicle_path, "bundle": bundle.path, "tire": tir_path, "lap_config": lap_config, "run_py": Path(__file__)}
    effective = {
        "mass_kg": ggv.mass, "cg_height_m": ggv.cg_height, "front_static_frac": ggv.front_static_frac,
        "wheelbase_m": ggv.wheelbase, "track_front_m": ggv.track_front, "track_rear_m": ggv.track_rear,
        "outer_max_deg": math.degrees(outer_max), "tire_support": support.summary(), "lltd_front": None,
        "from_vehicle_file": {
            "brake_front_bias": float(base["brake"]["front_bias"]), "rear_wheel_radius_m": float(base["rear"]["wheel"]["radius_m"]),
            "diff_kinetic_friction_ratio": float(base["powertrain"]["pDriveline"]["diff_kineticFrictionRatio"]),
            "max_drive_force_n": float(ggv.max_drive_force),
        },
        "from_lap_config": {"drive_power_limit_w": power_w},
    }
    extra = {"argv": sys.argv[1:], "bobsim_sha": os.environ.get("BOBSIM_SHA"), "effective": effective}
    write_resolved(out, resolve(study, files, extra))
    nominal_tire = inputs.tire(tir, NOMINAL[0])
    camber = inputs.static_camber_deg
    ggv.lltd = balanced_lltd(ggv, nominal_tire, curves[BASELINE], outer_max, support, camber, inputs.balance_radius_m)
    effective["lltd_front"] = float(ggv.lltd)
    resolved = resolve(study, files, extra)
    write_resolved(out, resolved)
    (out / "inputs.md").write_text(markdown_table(resolved), encoding="utf-8")
    nominal_car = Car(ggv, (nominal_tire,) * 4, ggv.lltd, support, camber)
    op_cars = {inputs.mu: nominal_car}
    if inputs.mu_sensitivity != inputs.mu:
        mu = inputs.mu_sensitivity
        sens_tire = {**tir, "LMUY": mu, "LMUX": mu, "LKY": inputs.lky_cases[NOMINAL[0]]}
        op_cars[mu] = Car(ggv, (sens_tire,) * 4, ggv.lltd, support, camber)
    steer_targets, steer_skipped = {}, {}
    for pct in inputs.steer_fix_targets_pct:
        law = ConstantAckermann(pct, ggv.track_front, ggv.wheelbase, outer_max)
        target, why = needed_mean_steer_deg(nominal_car, law, RADII_M[0], outer_max)
        if target is None:
            steer_skipped[f"{pct:+.0f}%"] = why
        else:
            steer_targets[pct] = target
    biases = (inputs.brake_bias, float(base["brake"]["front_bias"]))
    cases = build_cases(inputs, ggv, tir, curves, outer_max, biases, orion, steer_targets, nominal_car, front_geometry)
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
    kinetic = base["powertrain"]["pDriveline"]["diff_kineticFrictionRatio"]
    rears = {REAR_BASELINE: (0.0, 0.0, kinetic), "LSD": inputs.planned_diff + (kinetic,)}
    brakes = {brake_model(b): b for b in biases}
    brake_nominal = brake_model(inputs.brake_bias)
    rear_radius = float(base["rear"]["wheel"]["radius_m"])
    lky = inputs.lky_cases["lky_scaled"]
    sens_cars = {
        "LLTD -0.10": Car(ggv, (nominal_tire,) * 4, ggv.lltd - 0.10, support, camber),
        "LLTD +0.10": Car(ggv, (nominal_tire,) * 4, ggv.lltd + 0.10, support, camber),
        f"LKY {lky:.2f}": Car(ggv, ({**nominal_tire, "LKY": lky},) * 4, ggv.lltd, support, camber),
    }

    def map_case(car, radius, ax, model, labels, tol=2e-5):
        lon = Longitudinal(-ax, bias=brakes[model]) if ax < 0.0 else Longitudinal(-ax, diff=rears[model], wheel_radius=rear_radius)
        baseline = labels == {"mu": inputs.mu} and model in (REAR_BASELINE, brake_nominal)
        return {"kind": "map", "car": car, "curves": op_curves, "outer_max": outer_max, "radius": radius, "lon": lon, "tol": tol,
                "probe": baseline, "deriv": baseline and ax == 0.0 and radius in inputs.deriv_radii_m, "policy": inputs.policy,
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
        map_case(car, radius, ax, model, {"mu": inputs.mu, "variant": variant}, VARIANT_TOL)
        for variant, car in sens_cars.items() for radius in inputs.variant_radii_m for ax in inputs.variant_ax_g for model in models(ax, False)
    ]
    map_results = map_cases(solve_case, op_cases + sens_cases)
    op_rows = [row for result in map_results[:len(op_cases)] for row in result]
    sens_rows = [row for result in map_results[len(op_cases):] for row in result]
    for r in op_rows + sens_rows:
        r["lock_limited"] = bool(r["ay_max_g"] is not None and r["lock_margin_deg"] < LOCK_MARGIN_MIN_DEG)
    base_rows = [r for r in op_rows if r["rear"] in (REAR_BASELINE, brake_nominal)]
    design_rows = [r for r in op_rows + sens_rows if r["ax_g"] in inputs.variant_ax_g]
    robust = {"all": robust_region(design_rows, inputs.policy),
              "beyond_3p5": robust_region([r for r in design_rows if r["radius_m"] > RADII_M[0]], inputs.policy)}

    def lap_limits(car, bias):
        return {"brake_g": braking_limit_g(car, bias)["limit_g"], "drive_g": exit_accel_g(car, ggv.max_drive_force),
                "mass": car.mass, "power_w": power_w}

    qss_cases, caps, lap_excluded = [], [], []
    for mu, car in op_cars.items():
        limits = lap_limits(car, inputs.brake_bias)
        rows_mu = [r for r in base_rows if r["mu"] == mu]
        for name in op_curves:
            law_caps, excluded = check_map(rows_mu, name, limits["brake_g"], limits["drive_g"], inputs)
            if excluded:
                lap_excluded += [{"mu": mu, **e} for e in excluded]
                continue
            caps += [{"mu": mu, **c} for c in law_caps]
            qss_cases.append({"kind": "qss", "line": line["line"], "rows": rows_mu, "inputs": inputs, **limits, "labels": {"mu": mu, "curve": name}})
    if not any(c["labels"] == {"mu": inputs.mu, "curve": BASELINE} for c in qss_cases):
        raise RuntimeError(f"{BASELINE} has no complete physical envelope at grip {inputs.mu:g}. The lap reference needs it.")
    refined = [{**c, "refine": True} for c in qss_cases if c["labels"]["mu"] == inputs.mu and c["labels"]["curve"] in (BASELINE, *CONVERGENCE_CURVES)]
    fill_qss = cap_fill_cases(qss_cases, base_rows, caps, inputs)
    qss_results = map_cases(solve_case, qss_cases + refined + fill_qss)
    nominal_laps = qss_results[:len(qss_cases)]
    refined_laps = qss_results[len(qss_cases):len(qss_cases) + len(refined)]
    fill_laps = qss_results[len(qss_cases) + len(refined):]
    laps = lap_phases(nominal_laps, line["curvature"], base_rows, inputs)
    lap_time = {(r["curve"], r["refine"]): r["lap_time_s"] for r in nominal_laps + refined_laps if r["mu"] == inputs.mu}
    convergence = {}
    for name in CONVERGENCE_CURVES:
        if (name, True) not in lap_time:
            continue
        coarse = lap_time[(name, False)] - lap_time[(BASELINE, False)]
        fine = lap_time[(name, True)] - lap_time[(BASELINE, True)]
        convergence[name] = {"delta_s": round(coarse, 4), "delta_refined_s": round(fine, 4), "change_s": round(fine - coarse, 4)}
    convergence["band_s"] = round(2.0 * max(abs(v["change_s"]) for v in convergence.values()), 4) if convergence else None
    convergence["laws_not_modeled"] = [name for name in CONVERGENCE_CURVES if (name, True) not in lap_time]
    reference_lap = next(r for r in nominal_laps if r["mu"] == inputs.mu and r["curve"] == BASELINE)
    coverage = trim_coverage(reference_lap, line["curvature"], nominal_car, op_curves[BASELINE], outer_max, rears[REAR_BASELINE],
                             rear_radius, inputs.brake_bias)
    nominal_limits = next(c for c in qss_cases if c["labels"] == {"mu": inputs.mu, "curve": BASELINE})
    circle = circle_check(nominal_limits["rows"], *(nominal_limits[k] for k in ("brake_g", "drive_g", "mass", "power_w")), inputs)
    for f in fix:
        f.pop("table")
    for name, data in (("limits", rows), ("braking", braking), ("toe", toe), ("mass_cg", mass), ("operating_map", op_rows),
                       ("operating_map_sensitivity", sens_rows)):
        write_csv(out / f"{name}.csv", data)
    write_csv(out / "robust.csv", [
        {"law": law, "worst_shortfall_pct": v["worst_shortfall_pct"], "binding": v["binding"],
         **{f"in_region_{tol}": inside for tol, inside in v["in_region"].items()},
         "status": robust["all"]["status"], "n_supported": robust["all"]["n_supported"], "n_total": robust["all"]["n_total"]}
        for law, v in robust["all"]["laws"].items()
    ])
    write_csv(out / "robust_excluded.csv", robust["all"]["excluded"])

    nominal_base = {r["radius_m"]: r for r in rows if r["curve"] == DESIGN and is_nominal(r) and r["ay_max_g"] is not None}
    base_pct = {radius: r["ackermann_pct_at_limit"] for radius, r in nominal_base.items()}
    band_steer = [0.5 * (r["inner_deg"] + r["outer_deg"]) for r in nominal_base.values() if r["radius_m"] <= 8.0]
    steer_band = (min(band_steer), max(band_steer)) if band_steer else (0.0, 0.0)
    tables = {"Orion": orion_table, DESIGN: base_table}
    geometry = {"Orion": (orion_ggv.track_front, orion_ggv.wheelbase), DESIGN: (ggv.track_front, ggv.wheelbase)}
    plot_curves(out / "ackermann_curves.png", tables, geometry, steer_band)
    plot_grip(out / "grip_vs_ackermann.png", rows, base_pct, base_rows, inputs)

    table_rows = {}
    for radius in RADII_M:
        table_rows[f"{radius:g}m"] = {
            r["curve"]: {"ay_change_pct": None if r["ay_change_pct"] is None else round(r["ay_change_pct"], 2),
                         "limiting_axle": r.get("limiting_axle"), "bound_kind": r["bound_kind"]}
            for r in rows if is_nominal(r) and r["radius_m"] == radius
        }
    spread = {}
    for radius in RADII_M:
        for p in inputs.ackermann_pct:
            values = [r["ay_change_pct"] for r in rows if r["radius_m"] == radius and r["curve"] == f"{p:+d}%"]
            valid = [v for v in values if v is not None]
            spread.setdefault(f"{radius:g}m", {})[f"{p:+d}%"] = [
                round(min(valid), 2) if valid else None, round(max(valid), 2) if valid else None, len(valid), len(values)]
    slip_norm = [r for r in braking if r["combined_slip"] == "slip_norm"]
    ellipse = [r for r in braking if r["combined_slip"] == "ellipse"]

    summary = {
        "vehicle": {
            "note": f"{DESIGN} front hardpoints, team 2027 mass and CG, balanced LLTD, Orion rear and tire",
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
            "static_toe_deg": study.value("alignment.static_toe"), "static_camber_deg": inputs.static_camber_deg,
            "camber_variant_deg": inputs.camber_variant_deg, "vehicle_front_yml": "front geometry only",
        },
        "tire": {"template": base["front"]["tire"]["template"], "sha256": hashlib.sha256(tir_path.read_bytes()).hexdigest(),
                 "LMUY_LMUX": inputs.mu, "LKY_cases": inputs.lky_cases},
        "ackermann_pct_at_rack": {
            name: {f"{r:g}mm": round(float(ackermann_pct(at_rack(t, r, 1), at_rack(t, r, 2), *geometry[name])), 1) for r in (*REPORT_RACK_MM, inputs.rack_travel_mm)}
            for name, t in tables.items()
        },
        "nominal": table_rows,
        "ay_change_pct_range_all_cases": spread,
        "brake_model_gap_pts": max_change_gap(slip_norm, ellipse, ("brake_bias_front", "brake_g", "radius_m", "curve"), BRAKE_CURVES),
        "mass_gap_pts": max_change_gap(mass, [r for r in rows if is_nominal(r)], ("radius_m", "curve"), MASS_CURVES),
        "toe": toe_summary(toe, inputs.policy),
        "team_2027_inputs": {
            "mass_kg": round(inputs.mass_properties["mass"], 1), "cg_height_m": round(inputs.mass_properties["cg_height"], 4),
            "front_static_frac": inputs.mass_properties["front_static_frac"], "straight_braking_limit": braking_limit,
        },
        "lock_at_rack_travel": lock_check(base_table, nominal_car, TableCurve(base_table), outer_max, float(tir["WIDTH"]),
                                          inputs.rack_travel_mm),
        "front_base_linkage": {
            "arm_offset_mm": arm_offset_mm(base), "inner_wheel_at_travel": lock_geometry(base, inputs.rack_travel_mm),
            "tie_o_inboard_of_wheel_center_plane_mm": round(1000.0 * (
                front_geometry.points_m["wheel_center_m"][1] - front_geometry.points_m["tie_o_m"][1]), 1),
        },
        "steering_fix": fix,
        "steering_fix_skipped": steer_skipped,
        "first_principles_optimum": optimum_check(nominal_car, rows),
        "track_minimum_curvature_line": {
            "track": line["track"], "length_m": round(line["length_m"], 1), "corners_under_15m": len(corners),
            "corners": [{"turn_deg": round(math.degrees(t), 1), "min_radius_m": round(r, 2)} for t, r in corners],
        },
        "operating_map": {
            "radii_m": inputs.map_radii_m, "ax_g": inputs.map_ax_g, "curves": list(op_curves), "mu": list(op_cars),
            "rear_models": list(rears) + list(brakes), "rear_baseline": REAR_BASELINE, "variants": list(sens_cars),
            "variant_radii_m": inputs.variant_radii_m, "variant_ax_g": inputs.variant_ax_g,
            "lltd_front": {f"{mu:g}": round(car.lltd, 4) for mu, car in op_cars.items()},
            "cells_by_bound_kind": status_counts(op_rows + sens_rows),
            "caps": caps,
            "lap_excluded": lap_excluded,
            "lock_limited_cells": [{k: r[k] for k in ("mu", "radius_m", "ax_g", "curve", "ay_max_g") if k in r} | ({"variant": r["variant"]} if "variant" in r else {})
                                   for r in op_rows + sens_rows if r["lock_limited"]],
            "lock_margin_min_deg": {name: round(min((r["lock_margin_deg"] for r in base_rows if r["curve"] == name and r["ay_max_g"] is not None),
                                                    default=math.nan), 2)
                                    for name in op_curves},
        },
        "capabilities": {
            "braking_driveline": "open_axle",
            "braking_diff_coupling": "withheld",
            "vehicle_diff_use_lsd": bool(base["powertrain"]["pDriveline"].get("diff_use_lsd", False)),
            "note": "Hydraulic braking splits by bias with an open axle. No braking result models LSD coupling.",
        },
        "tire_support": {**support.summary(), "boundary_peaks": nominal_car.boundary_peaks},
        "comparison_policy": {"min_coverage": inputs.policy.min_coverage, "ay_floor_g": inputs.policy.ay_floor_g,
                              "tolerances_pct": list(inputs.policy.tolerances_pct),
                              "status": {k: study.fields[f"policy.{k}"].status for k in ("min_coverage", "ay_floor", "region_tolerances")}},
        "inputs": {"file": study.path.name, "sha256": study.sha256, "resolved": "inputs.resolved.json",
                   "front_geometry": {k: getattr(front_geometry, k) for k in ("label", "kind", "release_status", "source_file")}},
        "sweeps_by_bound_kind": {name: status_counts(data) for name, data in
                                 (("limits", rows), ("braking", braking), ("toe", toe), ("mass_cg", mass))},
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
