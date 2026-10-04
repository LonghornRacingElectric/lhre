import copy
import hashlib
import json
import os
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from scipy.interpolate import RegularGridInterpolator
from scipy.optimize import brentq

from _0_Utils.dyn_py import ModelInputs, Vehicle
from _0_Utils.dyn_py.parameters import G
from _0_Utils.kin_py.lookup import create_kinematics
from _0_Utils.lap_sim import GGVMap, TrackCorridor, simulate_transient_lap, solve_qss_lap
from _0_Utils.vehicle_io import repo_root
from tools.oval import oval_centerline, oval_layout
from tools.parallel import map_cases

FOUR_POST_METRICS = repo_root() / "_3_StandardSim/generated_results/four_post_eval_report_metrics.csv"
NOMINAL_RIDE_HEIGHT_M = 0.0762
ARMS = (("upper_fore_i_m", "upper_aft_i_m"), ("lower_fore_i_m", "lower_aft_i_m"))
CORNERS = ("fl", "fr", "rl", "rr")
TRAVEL_M = 0.025
AY_G, BRAKE_G, DRIVE_G = 1.3, 1.3, 0.6
TRACK_WIDTH_M = 4.0
TRACK_SPACING_M = 0.5
TRACKS = {
    "hairpin": {"length_m": 70.0, "mean_radius_m": (9.0, 9.0), "radius_rate_m_per_rad": (2.5, -2.5)},
    "template": {"length_m": 60.0, "mean_radius_m": (15.0, 9.0), "radius_rate_m_per_rad": (2.0, -1.5)},
    "sweeper": {"length_m": 40.0, "mean_radius_m": (25.0, 15.0), "radius_rate_m_per_rad": (0.0, 0.0)},
}
ANTI_TRACK, STEADY_TRACK = "hairpin", "sweeper"
SAMPLE_PERIOD_S = 0.005
STRAIGHT_CURVATURE_PER_M = 0.01
STEADY_AX_G = 0.05
SETTLE_S = 0.3
LOOKBACK_S = 0.5
LINK_TOLERANCE = 0.03
STEADY_FZ_TOLERANCE = 0.04
REFERENCE = "ad00_as00"
GRID_METRICS = (
    ("Front ride-height change in braking (mm/g)", "brake_ride_front_per_g", "+.1f"),
    ("Rear ride-height change in drive (mm/g)", "drive_ride_rear_per_g", "+.1f"),
    ("Pitch in braking (deg/g)", "brake_pitch_per_g", ".2f"),
    ("Pitch in drive (deg/g)", "drive_pitch_per_g", "+.2f"),
    ("Heave in braking (mm/g)", "brake_heave_per_g", "+.1f"),
    ("Heave in drive (mm/g)", "drive_heave_per_g", "+.1f"),
    ("Map downforce std over the lap (%)", "map_downforce_std_pct", ".1f"),
    ("Front Fz delay at brake onset (ms)", "brake_fz_front_delay_ms", ".1f"),
    ("Front Fz overshoot at brake onset (%)", "brake_fz_front_overshoot_pct", ".0f"),
    ("Rear Fz delay at drive onset (ms)", "drive_fz_rear_delay_ms", ".1f"),
    ("Rear Fz overshoot at drive onset (%)", "drive_fz_rear_overshoot_pct", ".0f"),
)
ANTI_LEVELS = (0.0, 0.2, 0.4, 0.6)
RC_LEVELS_M = (0.0, 0.025, 0.05)
def anti_name(dive, squat):
    return f"ad{round(100 * dive):02d}_as{round(100 * squat):02d}"


CARS = (
    {"name": "baseline"},
    *(
        {"name": anti_name(dive, squat), "anti_dive": dive, "anti_squat": squat}
        for dive in ANTI_LEVELS
        for squat in ANTI_LEVELS
    ),
    *({"name": f"rc{round(1000 * rc):02d}", "anti_dive": 0.0, "anti_squat": 0.0, "rc_m": rc} for rc in RC_LEVELS_M),
)


def with_axis_slope(vehicle, axle, slope):
    car = copy.deepcopy(vehicle)
    suspension = car[axle]["suspension"]
    wheel_center_x = suspension["wheel_center_m"][0]
    for fore, aft in ARMS:
        (fore_x, _, fore_z), (aft_x, _, aft_z) = suspension[fore], suspension[aft]
        center_z = fore_z + (aft_z - fore_z) * (wheel_center_x - fore_x) / (aft_x - fore_x)
        for key in (fore, aft):
            suspension[key][2] = center_z + slope * (suspension[key][0] - wheel_center_x)
    return car


def with_pivot_shift(vehicle, axle, shift_m):
    car = copy.deepcopy(vehicle)
    for arm in ARMS:
        for key in arm:
            car[axle]["suspension"][key][2] += shift_m
    return car


def anti_and_roll_center(coefficients, chassis):
    lever = chassis["wheelbase_m"] / chassis["cg_height_m"]
    front = chassis["brake_front"]
    return {
        "anti_dive_pct": -100.0 * coefficients[0, 0] * front * lever,
        "anti_lift_pct": 100.0 * coefficients[2, 0] * (1.0 - front) * lever,
        "anti_squat_pct": 100.0 * coefficients[2, 0] * lever,
        "rc_front_mm": -500.0 * coefficients[0, 1] * chassis["track_front_m"],
        "rc_rear_mm": -500.0 * coefficients[2, 1] * chassis["track_rear_m"],
    }


def static_geometry(car, chassis):
    coefficients = create_kinematics(car, mode="nonlinear").instant_links_at(np.zeros(4)).coefficient_matrix
    return anti_and_roll_center(coefficients, chassis)


def build_car(baseline, spec, chassis):
    car = baseline
    if "rc_m" in spec:
        for axle in ("front", "rear"):
            shift = brentq(
                lambda value: static_geometry(with_pivot_shift(car, axle, value), chassis)[f"rc_{axle}_mm"]
                - 1000.0 * spec["rc_m"],
                -0.06,
                0.06,
                xtol=1e-7,
            )
            car = with_pivot_shift(car, axle, shift)
    if "anti_dive" in spec:
        targets = (("front", "anti_dive_pct", spec["anti_dive"]), ("rear", "anti_squat_pct", spec["anti_squat"]))
        for axle, key, target in targets:
            slope = brentq(
                lambda value: static_geometry(with_axis_slope(car, axle, value), chassis)[key] - 100.0 * target,
                -0.2,
                0.2,
                xtol=1e-7,
            )
            car = with_axis_slope(car, axle, slope)
    return car


def geometry_report(car, baseline, chassis):
    kinematics = create_kinematics(car, mode="nonlinear")
    droop, static, bump = (kinematics.at(np.full(4, jounce)) for jounce in (-TRAVEL_M, 0.0, TRAVEL_M))
    droop_anti, bump_anti = (anti_and_roll_center(state.instant_links.coefficient_matrix, chassis) for state in (droop, bump))
    travel_mm = 2000.0 * TRAVEL_M
    pivot_move_mm = {
        axle: 1000.0 * max(abs(car[axle]["suspension"][key][2] - baseline[axle]["suspension"][key][2]) for arm in ARMS for key in arm)
        for axle in ("front", "rear")
    }
    return {
        **anti_and_roll_center(static.instant_links.coefficient_matrix, chassis),
        "anti_dive_droop_pct": droop_anti["anti_dive_pct"],
        "anti_dive_bump_pct": bump_anti["anti_dive_pct"],
        "anti_squat_droop_pct": droop_anti["anti_squat_pct"],
        "anti_squat_bump_pct": bump_anti["anti_squat_pct"],
        "camber_gain_front_deg_per_mm": float(np.degrees(bump.camber_rad[0] - droop.camber_rad[0])) / travel_mm,
        "camber_gain_rear_deg_per_mm": float(np.degrees(bump.camber_rad[2] - droop.camber_rad[2])) / travel_mm,
        "caster_change_front_deg": float(np.degrees(bump.caster_rad[0] - droop.caster_rad[0])),
        "pivot_move_front_mm": pivot_move_mm["front"],
        "pivot_move_rear_mm": pivot_move_mm["rear"],
    }


def loop_corridor(center):
    tangent = np.roll(center, -1, axis=0) - np.roll(center, 1, axis=0)
    normal = np.column_stack((-tangent[:, 1], tangent[:, 0])) / np.linalg.norm(tangent, axis=1)[:, None]
    return TrackCorridor(center + 0.5 * TRACK_WIDTH_M * normal, center - 0.5 * TRACK_WIDTH_M * normal)


def driver_target(chassis):
    speed = np.linspace(5.0, 30.0, 26)
    lateral = np.linspace(0.0, AY_G * G, 27)
    ellipse = np.sqrt(1.0 - (lateral / lateral[-1]) ** 2)
    drag = 0.5 * chassis["air_density_kg_m3"] * chassis["cd_area_m2"] * speed**2
    drive_force = np.minimum(chassis["peak_drive_force_n"], chassis["peak_drive_power_w"] / speed) - drag
    drive = np.clip(drive_force / chassis["mass_kg"], 0.0, DRIVE_G * G)
    return GGVMap.from_arrays(speed, lateral, np.outer(drive, ellipse), np.outer(np.full_like(speed, -BRAKE_G * G), ellipse))


def drive_lap(case):
    path, qss = case
    started = time.perf_counter()
    model = Vehicle.from_yaml(path, four_post_metrics_path=FOUR_POST_METRICS).model(14)
    lap = simulate_transient_lap(model, qss, stop_time_s=1.2 * qss.lap_time_s + 2.0, sample_period_s=SAMPLE_PERIOD_S)
    if not lap.completed_lap:
        raise RuntimeError(f"{path} did not finish a lap of length {qss.line.track_length_m:.0f} m")
    end = int(np.argmax(lap.unwrapped_progress_m >= qss.line.track_length_m)) + 1
    signals = {name: values[:end] for name, values in lap.transient.signals.items()}
    outputs = [
        model.evaluate(state, ModelInputs(steering_rad=float(steer)))
        for state, steer in zip(lap.transient.state[:end], signals["handwheelAngle"])
    ]
    acceleration = np.array([output.generalized_acceleration[:6] for output in outputs])
    yaw_rate, forward, lateral = signals["yaw_rate"], signals["u"], signals["v"]
    jounce = np.array([output.jounce_m for output in outputs])
    return {
        "time_s": lap.transient.time_s[:end],
        "station_m": lap.station_m[:end],
        "lateral_error_m": lap.lateral_error_m[:end],
        "speed_mps": np.hypot(forward, lateral),
        "ax_mps2": signals["accX"],
        "ay_mps2": signals["accY"],
        "acc_residual_mps2": float(
            max(
                np.max(np.abs(acceleration[:, 0] - yaw_rate * lateral - signals["accX"])),
                np.max(np.abs(acceleration[:, 1] + yaw_rate * forward - signals["accY"])),
            )
        ),
        "yaw_rate_radps": yaw_rate,
        "steer_rad": signals["handwheelAngle"],
        "pitch_rad": signals["pitch"],
        "roll_rad": signals["roll"],
        "heave_m": signals["z"],
        "fz_n": np.array([output.normal_loads_n for output in outputs]),
        "link_n": np.array([output.geometric_vertical_forces_n for output in outputs]),
        "jounce_m": jounce,
        "ride_m": np.column_stack([signals[f"unsprung_z_{corner}"] for corner in CORNERS]) - jounce,
        "lap_time_s": lap.lap_time_s,
        "wall_s": time.perf_counter() - started,
    }


def trace_channels(raw, downforce_map, chassis):
    ride_front = NOMINAL_RIDE_HEIGHT_M + raw["ride_m"][:, :2].mean(axis=1)
    ride_rear = NOMINAL_RIDE_HEIGHT_M + raw["ride_m"][:, 2:].mean(axis=1)
    map_ratio = downforce_map(np.column_stack((ride_front, ride_rear))) / downforce_map((NOMINAL_RIDE_HEIGHT_M, NOMINAL_RIDE_HEIGHT_M))
    channels = {
        "time_s": raw["time_s"],
        "station_m": raw["station_m"],
        "speed_mps": raw["speed_mps"],
        "ax_g": raw["ax_mps2"] / G,
        "ay_g": raw["ay_mps2"] / G,
        "yaw_rate_deg_s": np.degrees(raw["yaw_rate_radps"]),
        "steer_deg": np.degrees(raw["steer_rad"]),
        "pitch_deg": np.degrees(raw["pitch_rad"]),
        "roll_deg": np.degrees(raw["roll_rad"]),
        "heave_mm": 1000.0 * raw["heave_m"],
        "ride_front_mm": 1000.0 * ride_front,
        "ride_rear_mm": 1000.0 * ride_rear,
        "jounce_front_mm": 1000.0 * raw["jounce_m"][:, :2].mean(axis=1),
        "jounce_rear_mm": 1000.0 * raw["jounce_m"][:, 2:].mean(axis=1),
        "map_downforce_pct": 100.0 * (map_ratio - 1.0),
        "map_downforce_n": downforce_map((NOMINAL_RIDE_HEIGHT_M, NOMINAL_RIDE_HEIGHT_M)) * map_ratio * (raw["speed_mps"] / chassis["aero_reference_speed_mps"]) ** 2,
        "lateral_error_m": raw["lateral_error_m"],
    }
    for index, corner in enumerate(CORNERS):
        channels[f"fz_{corner}_n"] = raw["fz_n"][:, index]
    for index, corner in enumerate(CORNERS):
        channels[f"link_{corner}_n"] = raw["link_n"][:, index]
    channels["fz_front_n"] = raw["fz_n"][:, :2].sum(axis=1)
    channels["fz_rear_n"] = raw["fz_n"][:, 2:].sum(axis=1)
    channels["link_front_n"] = raw["link_n"][:, :2].sum(axis=1)
    channels["link_rear_n"] = raw["link_n"][:, 2:].sum(axis=1)
    return channels


def runs(mask):
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(int), [0]))))
    return list(zip(edges[::2], edges[1::2]))


def straight_mask(trace, line):
    return np.abs(np.interp(trace["station_m"], line.station_m, line.curvature_per_m)) < STRAIGHT_CURVATURE_PER_M


def event_windows(trace, qss):
    line = qss.line
    target_ax = np.interp(trace["station_m"], line.station_m, qss.longitudinal_acceleration_mps2)
    straight = straight_mask(trace, line)
    events = {
        "brake": straight & (target_ax < -0.5 * BRAKE_G * G),
        "drive": straight & (target_ax > 0.25 * G),
        "corner": ~straight,
        "steady": ~straight & (np.abs(target_ax) < STEADY_AX_G * G),
    }
    time_s = trace["time_s"]
    windows = {}
    for kind, mask in events.items():
        spans = [
            (start, stop)
            for start, stop in runs(mask)
            if time_s[stop - 1] - time_s[start] > 2.0 * SETTLE_S and time_s[start] - time_s[0] >= LOOKBACK_S
        ]
        settled = [
            index
            for start, stop in spans
            for index in range(start, stop)
            if min(trace["time_s"][index] - trace["time_s"][start], trace["time_s"][stop - 1] - trace["time_s"][index]) >= SETTLE_S
        ]
        windows[kind] = {"spans": spans, "settled": np.asarray(settled, dtype=int)}
    return windows


def per_g(trace, indices, sign, channel, reference):
    regressors = np.column_stack((sign * trace["ax_g"][indices], trace["speed_mps"][indices] ** 2))
    return float(np.linalg.lstsq(regressors, trace[channel][indices] - reference, rcond=None)[0][0])


def onset_response(trace, span, cause, effect):
    time_s = trace["time_s"] - trace["time_s"][span[0]]
    before = (time_s >= -LOOKBACK_S) & (time_s < -0.8 * LOOKBACK_S)
    after = (time_s >= SETTLE_S) & (time_s < 2.0 * SETTLE_S)
    search = (time_s >= -0.8 * LOOKBACK_S) & (time_s < 2.0 * SETTLE_S)

    def progress(channel):
        initial = np.mean(trace[channel][before])
        return (trace[channel] - initial) / (np.mean(trace[channel][after]) - initial)

    def half_time(values):
        index = int(np.flatnonzero(search & (values >= 0.5))[0])
        fraction = (0.5 - values[index - 1]) / (values[index] - values[index - 1])
        return float(time_s[index - 1] + fraction * (time_s[index] - time_s[index - 1]))

    response = progress(effect)
    return {
        "delay_ms": 1000.0 * (half_time(response) - half_time(progress(cause))),
        "overshoot_pct": 100.0 * max(0.0, float(np.max(response[search])) - 1.0),
    }


def mean_response(trace, spans, cause, effect):
    responses = [onset_response(trace, span, cause, effect) for span in spans]
    return {key: float(np.mean([response[key] for response in responses])) for key in responses[0]}


def track_metrics(trace, qss, chassis):
    metrics = {
        "lap_time_s": float(trace["time_s"][-1] - trace["time_s"][0]),
        "rms_lateral_error_m": float(np.sqrt(np.mean(trace["lateral_error_m"] ** 2))),
        "min_fz_n": float(min(np.min(trace[f"fz_{corner}_n"]) for corner in CORNERS)),
        "map_downforce_std_pct": float(np.std(trace["map_downforce_pct"])),
        "map_downforce_range_pct": float(np.ptp(trace["map_downforce_pct"])),
        "ride_front_min_mm": float(np.min(trace["ride_front_mm"])),
        "ride_front_max_mm": float(np.max(trace["ride_front_mm"])),
        "ride_rear_min_mm": float(np.min(trace["ride_rear_mm"])),
        "ride_rear_max_mm": float(np.max(trace["ride_rear_mm"])),
    }
    windows = event_windows(trace, qss)
    references = {
        "ride_front_mm": 1000.0 * NOMINAL_RIDE_HEIGHT_M,
        "ride_rear_mm": 1000.0 * NOMINAL_RIDE_HEIGHT_M,
        "fz_front_n": chassis["static_front_n"],
        "fz_rear_n": chassis["static_rear_n"],
    }
    for kind, sign, axle, onset in (("brake", -1.0, "front", "fz_front_n"), ("drive", 1.0, "rear", "fz_rear_n")):
        settled = windows[kind]["settled"]
        if settled.size < 10:
            continue
        for channel in ("pitch_deg", "heave_mm", "ride_front_mm", "ride_rear_mm", "jounce_front_mm", "jounce_rear_mm", "fz_front_n", "fz_rear_n", "link_front_n", "link_rear_n"):
            metrics[f"{kind}_{channel.rsplit('_', 1)[0]}_per_g"] = per_g(trace, settled, sign, channel, references.get(channel, 0.0))
        metrics[f"{kind}_ax_g"] = float(np.mean(trace["ax_g"][settled]))
        for side in ("front", "rear"):
            metrics[f"{kind}_link_share_{side}_pct"] = 100.0 * metrics[f"{kind}_link_{side}_per_g"] / metrics[f"{kind}_fz_{side}_per_g"]
        for name, value in mean_response(trace, windows[kind]["spans"], "ax_g", onset).items():
            metrics[f"{kind}_fz_{axle}_{name}"] = value
    if windows["steady"]["settled"].size >= 10:
        metrics["steady"] = lateral_metrics(trace, windows["steady"]["settled"])
        metrics["turn_in_yaw_delay_ms"] = mean_response(trace, windows["corner"]["spans"], "steer_deg", "yaw_rate_deg_s")["delay_ms"]
        roll = mean_response(trace, windows["corner"]["spans"], "steer_deg", "roll_deg")
        metrics["turn_in_roll_delay_ms"], metrics["turn_in_roll_overshoot_pct"] = roll["delay_ms"], roll["overshoot_pct"]
    return metrics


def lateral_metrics(trace, indices):
    ay = trace["ay_g"][indices]
    transfer_front = 0.5 * (trace["fz_fr_n"] - trace["fz_fl_n"])[indices]
    transfer_rear = 0.5 * (trace["fz_rr_n"] - trace["fz_rl_n"])[indices]
    return {
        "ay_g": float(np.mean(ay)),
        "roll_deg_per_g": float(np.mean(trace["roll_deg"][indices]) / np.mean(ay)),
        "heave_mm": float(np.mean(trace["heave_mm"][indices])),
        "ride_front_mm": float(np.mean(trace["ride_front_mm"][indices])),
        "ride_rear_mm": float(np.mean(trace["ride_rear_mm"][indices])),
        "fz_fr_n": float(np.mean(trace["fz_fr_n"][indices])),
        "lltd_front_pct": float(100.0 * np.mean(transfer_front) / np.mean(transfer_front + transfer_rear)),
    }


def checks(cars, summary, residuals):
    metric = lambda car, track: summary["cars"][car]["tracks"][track]
    anti_cars = [car["name"] for car in cars if "anti_dive" in car and "rc_m" not in car]
    rc_cars = sorted((car["name"] for car in cars if "rc_m" in car or car["name"] == REFERENCE), key=lambda name: summary["cars"][name]["geometry"]["rc_front_mm"])
    chassis = summary["chassis"]
    transfer_n_per_g = chassis["mass_kg"] * G * chassis["cg_height_m"] / chassis["wheelbase_m"]
    events = (("brake", "front", "anti_dive_pct", 1.0), ("brake", "rear", "anti_lift_pct", -1.0), ("drive", "rear", "anti_squat_pct", 1.0))
    results = []
    for car in anti_cars:
        for kind, side, key, sign in events:
            achieved = metric(car, ANTI_TRACK)[f"{kind}_link_{side}_per_g"]
            design = sign * summary["cars"][car]["geometry"][key] / 100.0 * transfer_n_per_g
            share = metric(car, ANTI_TRACK)[f"{kind}_link_share_{side}_pct"]
            passed = abs(achieved - design) <= LINK_TOLERANCE * transfer_n_per_g
            results.append((f"1 link force {car} {kind} {side}", passed, f"{achieved:.1f} vs {design:.1f} N/g, link share {share:.1f} %"))
    reference = metric(REFERENCE, ANTI_TRACK)
    for car in anti_cars:
        for kind, side, *_ in events:
            ratio = metric(car, ANTI_TRACK)[f"{kind}_jounce_{side}_per_g"] / reference[f"{kind}_jounce_{side}_per_g"]
            spring_share = lambda name: 1.0 - metric(name, ANTI_TRACK)[f"{kind}_link_share_{side}_pct"] / 100.0
            expected = spring_share(car) / spring_share(REFERENCE)
            results.append((f"2 spring share {car} {kind} {side}", abs(ratio / expected - 1.0) <= 0.10, f"{ratio:.3f} vs {expected:.3f}"))
    for kind, side in (("brake", "front"), ("brake", "rear"), ("drive", "rear")):
        values = np.array([metric(car, ANTI_TRACK)[f"{kind}_fz_{side}_per_g"] for car in anti_cars])
        passed = np.ptp(values) <= STEADY_FZ_TOLERANCE * np.abs(np.mean(values))
        results.append((f"3 steady Fz {kind} {side}", passed, f"{values.min():.1f}..{values.max():.1f} N/g"))
    for key in ("fz_fr_n", "roll_deg_per_g", "ride_front_mm"):
        values = np.array([metric(car, STEADY_TRACK)["steady"][key] for car in anti_cars])
        limit = 0.02 if key.startswith("fz") else 0.01
        results.append((f"3/4 {STEADY_TRACK} steady {key}", np.ptp(values) <= limit * np.abs(np.mean(values)), f"{values.min():.3f}..{values.max():.3f}"))
    worst = max(residuals.values())
    results.append(("5 re-evaluated accX/accY", worst < 1e-6, f"{worst:.2e} m/s2"))
    dive = reference["brake_ride_front_per_g"]
    results.append(("6 front ride height falls in braking", dive < 0.0, f"{dive:.2f} mm/g"))
    roll = [metric(car, STEADY_TRACK)["steady"]["roll_deg_per_g"] for car in rc_cars]
    results.append(("7 roll gradient falls with RC", bool(np.all(np.diff(roll) < 0.0)), ", ".join(f"{value:.3f}" for value in roll)))
    return results


def markdown_table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "| --- |" + " ---: |" * (len(header) - 1)]
    return "\n".join(lines + ["| " + " | ".join(row) + " |" for row in rows]) + "\n"


def write_tables(summary, out_dir):
    cars = summary["cars"]
    grids = [
        markdown_table(
            [title] + [f"AS {round(100 * squat)} %" for squat in ANTI_LEVELS],
            [
                [f"AD {round(100 * dive)} %"] + [format(cars[anti_name(dive, squat)]["tracks"][ANTI_TRACK][key], spec) for squat in ANTI_LEVELS]
                for dive in ANTI_LEVELS
            ],
        )
        for title, key, spec in GRID_METRICS
    ]
    (out_dir / "anti.md").write_text("\n".join(grids))
    levels = [(dive, squat) for dive in ANTI_LEVELS for squat in ANTI_LEVELS]
    regressors = np.column_stack((np.ones(len(levels)), 10.0 * np.asarray(levels)))
    fits = []
    for title, key, spec in GRID_METRICS:
        values = np.array([cars[anti_name(dive, squat)]["tracks"][ANTI_TRACK][key] for dive, squat in levels])
        coefficients = np.linalg.lstsq(regressors, values, rcond=None)[0]
        error = np.max(np.abs(regressors @ coefficients - values))
        digits = int(spec[-2]) + 1
        fits.append([title, format(coefficients[0], spec), *(f"{value:+.{digits}f}" for value in coefficients[1:]), f"{error:.{digits}f}"])
    (out_dir / "fits.md").write_text(markdown_table(["Metric", "Fit at AD 0 / AS 0", "Per +10 % anti-dive", "Per +10 % anti-squat", "Max fit error"], fits))
    rc_names = sorted((name for name in cars if name.startswith("rc") or name == REFERENCE), key=lambda name: cars[name]["geometry"]["rc_front_mm"])
    (out_dir / "rc.md").write_text(
        markdown_table(
            ["Car", "RC front / rear (mm)", "Pivot move front / rear (mm)", "Roll (deg/g)", "Front LLTD (%)", "Heave (mm)", "Yaw delay (ms)", "Roll overshoot (%)"],
            [
                [
                    name,
                    f"{geometry['rc_front_mm']:.0f} / {geometry['rc_rear_mm']:.0f}",
                    f"{geometry['pivot_move_front_mm']:.1f} / {geometry['pivot_move_rear_mm']:.1f}",
                    f"{steady['steady']['roll_deg_per_g']:.3f}",
                    f"{steady['steady']['lltd_front_pct']:.1f}",
                    f"{steady['steady']['heave_mm']:+.1f}",
                    f"{steady['turn_in_yaw_delay_ms']:.0f}",
                    f"{steady['turn_in_roll_overshoot_pct']:.0f}",
                ]
                for name in rc_names
                for geometry, steady in [(cars[name]["geometry"], cars[name]["tracks"][STEADY_TRACK])]
            ],
        )
    )
    (out_dir / "geometry.md").write_text(
        markdown_table(
            ["Car", "Anti-dive (%)", "Anti-squat (%)", "Anti-lift (%)", "RC front / rear (mm)", "Anti-dive at -25 / +25 mm (%)", "Anti-squat at -25 / +25 mm (%)", "Camber gain front / rear (deg/mm)", "Pivot move front / rear (mm)"],
            [
                [
                    name,
                    f"{geometry['anti_dive_pct']:.1f}",
                    f"{geometry['anti_squat_pct']:.1f}",
                    f"{geometry['anti_lift_pct']:.1f}",
                    f"{geometry['rc_front_mm']:.0f} / {geometry['rc_rear_mm']:.0f}",
                    f"{geometry['anti_dive_droop_pct']:.1f} / {geometry['anti_dive_bump_pct']:.1f}",
                    f"{geometry['anti_squat_droop_pct']:.1f} / {geometry['anti_squat_bump_pct']:.1f}",
                    f"{geometry['camber_gain_front_deg_per_mm']:.3f} / {geometry['camber_gain_rear_deg_per_mm']:.3f}",
                    f"{geometry['pivot_move_front_mm']:.1f} / {geometry['pivot_move_rear_mm']:.1f}",
                ]
                for name in cars
                for geometry in [cars[name]["geometry"]]
            ],
        )
    )
    (out_dir / "tracks.md").write_text(
        markdown_table(
            ["Track", "L (m)", "R0 / R1 (m)", "R0' / R1' (m/rad)", "Turn 0 / 1 (deg)", "Straights (m)", "Lap (m)", "Target lap (s)", "Lap time, all cars (s)", "RMS lateral error, all cars (m)"],
            [
                [
                    track,
                    f"{parameters['length_m']:g}",
                    " / ".join(f"{value:g}" for value in parameters["mean_radius_m"]),
                    " / ".join(f"{value:g}" for value in parameters["radius_rate_m_per_rad"]),
                    " / ".join(f"{corner['turn_deg']:.0f}" for corner in layout["corners"]),
                    " / ".join(f"{value:.1f}" for value in layout["straights_m"]),
                    f"{layout['lap_m']:.0f}",
                    f"{layout['target_lap_s']:.2f}",
                    f"{min(times):.2f}..{max(times):.2f}",
                    f"{min(errors):.3f}..{max(errors):.3f}",
                ]
                for track, layout in summary["tracks"].items()
                for parameters in [TRACKS[track]]
                for times, errors in [
                    (
                        [car["tracks"][track]["lap_time_s"] for car in cars.values()],
                        [car["tracks"][track]["rms_lateral_error_m"] for car in cars.values()],
                    )
                ]
            ],
        )
    )


def plot_onset(traces, laps, out_dir, file_name, track, kind, cars, channels, title):
    figure, axes = plt.subplots(len(channels), 1, sharex=True, figsize=(6, 2 * len(channels)))
    for name, legend in cars:
        trace = traces[(name, track)]
        start = event_windows(trace, laps[track])[kind]["spans"][0][0]
        time_s = trace["time_s"] - trace["time_s"][start]
        window = (time_s >= -0.2) & (time_s <= 1.0)
        for axis, (channel, label) in zip(axes, channels):
            axis.plot(time_s[window], trace[channel][window], label=legend)
            axis.set_ylabel(label)
    axes[0].legend(fontsize=8)
    axes[0].set_title(title)
    axes[-1].set_xlabel(f"time from {kind} event start (s)")
    figure.tight_layout()
    figure.savefig(out_dir / file_name, dpi=110)
    plt.close(figure)


def plot_onsets(traces, laps, summary, out_dir):
    cars = summary["cars"]
    rc_names = sorted((name for name in cars if name.startswith("rc") or name == REFERENCE), key=lambda name: cars[name]["geometry"]["rc_front_mm"])
    plot_onset(
        traces, laps, out_dir, "braking.png", ANTI_TRACK, "brake",
        [(anti_name(dive, 0.0), f"anti-dive {round(100 * dive)} %") for dive in ANTI_LEVELS],
        (("ax_g", "ax (g)"), ("fz_front_n", "front axle Fz (N)"), ("pitch_deg", "pitch (deg)"), ("ride_front_mm", "front ride height (mm)")),
        f"{ANTI_TRACK}, first brake event, anti-squat 0 %",
    )
    plot_onset(
        traces, laps, out_dir, "drive.png", ANTI_TRACK, "drive",
        [(anti_name(0.0, squat), f"anti-squat {round(100 * squat)} %") for squat in ANTI_LEVELS],
        (("ax_g", "ax (g)"), ("fz_rear_n", "rear axle Fz (N)"), ("pitch_deg", "pitch (deg)"), ("ride_rear_mm", "rear ride height (mm)")),
        f"{ANTI_TRACK}, first drive event, anti-dive 0 %",
    )
    plot_onset(
        traces, laps, out_dir, "turn_in.png", STEADY_TRACK, "corner",
        [(name, "RC {rc_front_mm:.0f} / {rc_rear_mm:.0f} mm".format(**cars[name]["geometry"])) for name in rc_names],
        (("steer_deg", "steer (deg)"), ("yaw_rate_deg_s", "yaw rate (deg/s)"), ("roll_deg", "roll (deg)"), ("heave_mm", "heave (mm)"), ("fz_fr_n", "outer front Fz (N)")),
        f"{STEADY_TRACK}, first corner entry, anti 0 / 0",
    )


LAP_PANELS = (
    ("speed (m/s)", (("speed_mps", "speed"),)),
    ("acceleration (g)", (("ax_g", "ax"), ("ay_g", "ay"))),
    ("angle (deg)", (("pitch_deg", "pitch"), ("roll_deg", "roll"))),
    ("ride height (mm)", (("ride_front_mm", "front"), ("ride_rear_mm", "rear"))),
    ("Fz (N)", tuple((f"fz_{corner}_n", corner) for corner in CORNERS)),
    ("map downforce (%)", (("map_downforce_pct", "change from static"),)),
)
MAP_CHANNELS = (
    ("speed_mps", "speed (m/s)"),
    ("ax_g", "ax (g)"),
    ("ride_front_mm", "front ride height (mm)"),
    ("ride_rear_mm", "rear ride height (mm)"),
    ("map_downforce_pct", "map downforce (%)"),
)


def plot_laps(traces, laps, out_dir):
    for track, lap in laps.items():
        trace = traces[("baseline", track)]
        steps = np.mod(np.diff(trace["station_m"]), lap.line.track_length_m)
        station = trace["station_m"][0] + np.concatenate(([0.0], np.cumsum(steps)))
        corners = runs(~straight_mask(trace, lap.line))
        figure, axes = plt.subplots(len(LAP_PANELS), 1, sharex=True, figsize=(9, 2 * len(LAP_PANELS)))
        for axis, (label, channels) in zip(axes, LAP_PANELS):
            for start, stop in corners:
                axis.axvspan(station[start], station[stop - 1], color="0.9")
            for channel, legend in channels:
                axis.plot(station, trace[channel], label=legend)
            axis.set_ylabel(label)
            axis.grid(alpha=0.3)
        axes[0].plot(lap.line.station_m, lap.speed_mps, "--", color="0.4", label="driver target")
        for axis in axes:
            axis.legend(fontsize=7, loc="upper right")
        axes[0].set_title(f"{track}, baseline car, one lap; grey = corner")
        axes[-1].set_xlabel("station (m)")
        figure.tight_layout()
        figure.savefig(out_dir / f"lap_{track}.png", dpi=110)
        plt.close(figure)


def plot_track_maps(traces, laps, out_dir):
    figure, axes = plt.subplots(len(laps), len(MAP_CHANNELS), figsize=(3.6 * len(MAP_CHANNELS), 3.2 * len(laps)), squeeze=False)
    for row, (track, lap) in zip(axes, laps.items()):
        trace = traces[("baseline", track)]
        line, period = lap.line, lap.line.track_length_m
        x_m = np.interp(trace["station_m"], line.station_m, line.x_m, period=period)
        y_m = np.interp(trace["station_m"], line.station_m, line.y_m, period=period)
        for axis, (channel, label) in zip(row, MAP_CHANNELS):
            points = axis.scatter(x_m, y_m, c=trace[channel], s=3, cmap="viridis")
            axis.plot(x_m[0], y_m[0], "k>", markersize=6)
            figure.colorbar(points, ax=axis, label=label, shrink=0.8)
            axis.set_aspect("equal")
            axis.set_title(f"{track}: {label}", fontsize=9)
            axis.tick_params(labelsize=7)
    figure.suptitle("Baseline car, one lap; x and y in m; arrow = start")
    figure.tight_layout()
    figure.savefig(out_dir / "track_maps.png", dpi=110)
    plt.close(figure)


def plot_grids(summary, out_dir):
    cars = summary["cars"]
    columns = 4
    rows = -(-len(GRID_METRICS) // columns)
    ticks = range(len(ANTI_LEVELS)), [f"{round(100 * level)}" for level in ANTI_LEVELS]
    figure, axes = plt.subplots(rows, columns, figsize=(4 * columns, 3.4 * rows))
    for axis, (title, key, spec) in zip(axes.flat, GRID_METRICS):
        values = np.array([[cars[anti_name(dive, squat)]["tracks"][ANTI_TRACK][key] for squat in ANTI_LEVELS] for dive in ANTI_LEVELS])
        image = axis.imshow(values, origin="lower", cmap="viridis")
        for (row, column), value in np.ndenumerate(values):
            axis.text(column, row, format(value, spec), ha="center", va="center", fontsize=8, color="k" if image.norm(value) > 0.6 else "w")
        axis.set_xticks(*ticks)
        axis.set_yticks(*ticks)
        axis.set_xlabel("anti-squat (%)")
        axis.set_ylabel("anti-dive (%)")
        axis.set_title(title, fontsize=9)
        figure.colorbar(image, ax=axis, shrink=0.8)
    for axis in axes.flat[len(GRID_METRICS):]:
        axis.set_visible(False)
    figure.suptitle(f"{ANTI_TRACK}, 16 anti cars")
    figure.tight_layout()
    figure.savefig(out_dir / "anti_grid.png", dpi=110)
    plt.close(figure)


def plot_trends(summary, out_dir):
    cars = summary["cars"]
    anti = 100.0 * np.asarray(ANTI_LEVELS)
    front = [cars[anti_name(dive, 0.0)]["tracks"][ANTI_TRACK] for dive in ANTI_LEVELS]
    rear = [cars[anti_name(0.0, squat)]["tracks"][ANTI_TRACK] for squat in ANTI_LEVELS]
    figure, axes = plt.subplots(2, 2, figsize=(9, 7))
    panels = (
        ("ride-height change (mm/g)", "brake_ride_front_per_g", "drive_ride_rear_per_g"),
        ("heave (mm/g)", "brake_heave_per_g", "drive_heave_per_g"),
        ("axle Fz delay at onset (ms)", "brake_fz_front_delay_ms", "drive_fz_rear_delay_ms"),
    )
    for axis, (label, brake_key, drive_key) in zip(axes.flat, panels):
        axis.plot(anti, [metrics[brake_key] for metrics in front], "o-", label="front, braking, vs anti-dive")
        axis.plot(anti, [metrics[drive_key] for metrics in rear], "s-", label="rear, drive, vs anti-squat")
        axis.set_xlabel("anti (%)")
        axis.set_ylabel(label)
        axis.grid(alpha=0.3)
    axes[0, 0].legend(fontsize=8)
    rc_names = sorted((name for name in cars if name.startswith("rc") or name == REFERENCE), key=lambda name: cars[name]["geometry"]["rc_front_mm"])
    axis = axes[1, 1]
    axis.plot([cars[name]["geometry"]["rc_front_mm"] for name in rc_names], [cars[name]["tracks"][STEADY_TRACK]["steady"]["roll_deg_per_g"] for name in rc_names], "o-")
    axis.set_xlabel("front roll-center height (mm)")
    axis.set_ylabel(f"roll gradient, {STEADY_TRACK} (deg/g)")
    axis.grid(alpha=0.3)
    figure.suptitle(f"{ANTI_TRACK} events at anti-squat 0 % or anti-dive 0 %; roll at anti 0 / 0")
    figure.tight_layout()
    figure.savefig(out_dir / "trends.png", dpi=110)


def main():
    out_dir = Path(os.environ["OUT_DIR"])
    vehicle_path = Path(os.environ["BOBSIM_VEHICLE"])
    if not FOUR_POST_METRICS.exists():
        raise FileNotFoundError(
            f"{FOUR_POST_METRICS} is missing. Run 'make records', 'make bobsim T=standard-build-four-post' "
            "and 'make bobsim T=standard-eval-four-post' first."
        )
    baseline = yaml.safe_load(vehicle_path.read_text())
    parameters = Vehicle.from_yaml(vehicle_path, four_post_metrics_path=FOUR_POST_METRICS).parameters
    chassis = {
        "mass_kg": parameters.mass_kg,
        "cg_height_m": -float(parameters.corner_positions[0, 2]),
        "wheelbase_m": parameters.wheelbase_m,
        "brake_front": parameters.brake_distribution_front,
        "track_front_m": parameters.track_front_m,
        "track_rear_m": parameters.track_rear_m,
        "peak_drive_force_n": parameters.peak_drive_force_n,
        "peak_drive_power_w": parameters.peak_drive_power_w,
        "cd_area_m2": parameters.cd_area_m2,
        "air_density_kg_m3": parameters.rho_air_kg_m3,
        "static_front_n": float(sum(parameters.static_wheel_loads_n[:2])),
        "static_rear_n": float(sum(parameters.static_wheel_loads_n[2:])),
        "aero_reference_speed_mps": float(baseline["aero"]["reference_speed_m_per_s"]),
    }
    aero = baseline["aero"]
    downforce_map = RegularGridInterpolator(
        (aero["front_ride_height_grid_m"], aero["rear_ride_height_grid_m"]), np.asarray(aero["downforce_table_n"], dtype=float)
    )

    car_dir = out_dir / "cars"
    car_dir.mkdir(parents=True, exist_ok=True)
    geometry = {}
    for spec in CARS:
        car = baseline if spec["name"] == "baseline" else build_car(baseline, spec, chassis)
        (car_dir / f"{spec['name']}.yml").write_text(yaml.safe_dump(car, sort_keys=False))
        geometry[spec["name"]] = geometry_report(car, baseline, chassis)

    target = driver_target(chassis)
    corridors = {name: loop_corridor(oval_centerline(**parameters, spacing_m=TRACK_SPACING_M)) for name, parameters in TRACKS.items()}
    laps = {name: solve_qss_lap(corridor.line_from_offsets(np.zeros(corridor.gate_count)), target) for name, corridor in corridors.items()}
    cases = [(spec["name"], track) for spec in CARS for track in laps]
    started = time.perf_counter()
    raw = dict(zip(cases, map_cases(drive_lap, [(str(car_dir / f"{car}.yml"), laps[track]) for car, track in cases])))
    print(f"wall {time.perf_counter() - started:.0f} s; per case: " + ", ".join(f"{car}/{track} {result['wall_s']:.0f} s" for (car, track), result in raw.items()))

    trace_dir = out_dir / "traces"
    trace_dir.mkdir(exist_ok=True)
    summary = {
        "chassis": chassis,
        "four_post_metrics_sha256": hashlib.sha256(FOUR_POST_METRICS.read_bytes()).hexdigest(),
        "driver": {"ay_g": AY_G, "brake_g": BRAKE_G, "drive_g": DRIVE_G},
        "tracks": {track: {**oval_layout(**TRACKS[track]), "target_lap_s": lap.lap_time_s} for track, lap in laps.items()},
        "cars": {spec["name"]: {"geometry": geometry[spec["name"]], "tracks": {}} for spec in CARS},
    }
    traces = {}
    for (car, track), result in raw.items():
        trace = trace_channels(result, downforce_map, chassis)
        traces[(car, track)] = trace
        np.savetxt(trace_dir / f"{car}_{track}.csv", np.column_stack(list(trace.values())), delimiter=",", header=",".join(trace), comments="", fmt="%.6g")
        summary["cars"][car]["tracks"][track] = track_metrics(trace, laps[track], chassis)

    results = checks(CARS, summary, {case: result["acc_residual_mps2"] for case, result in raw.items()})
    summary["checks"] = {name: {"pass": bool(passed), "detail": detail} for name, passed, detail in results}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    write_tables(summary, out_dir)
    plot_onsets(traces, laps, summary, out_dir)
    plot_trends(summary, out_dir)
    plot_laps(traces, laps, out_dir)
    plot_track_maps(traces, laps, out_dir)
    plot_grids(summary, out_dir)

    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    failed = [name for name, passed, _ in results if not passed]
    if failed:
        raise AssertionError(f"{len(failed)} checks failed: {failed}")


if __name__ == "__main__":
    main()
