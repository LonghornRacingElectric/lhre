import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

X = np.array([1.0, 0.0, 0.0])
Z = np.array([0.0, 0.0, 1.0])
LINKS = (
    ("suspension", "lower_fore_i_m", "lower_o_m"),
    ("suspension", "lower_aft_i_m", "lower_o_m"),
    ("suspension", "upper_fore_i_m", "upper_o_m"),
    ("suspension", "upper_aft_i_m", "upper_o_m"),
    ("steering", "rack_pickup_m", "tie_o_m"),
)


def instant_center_coeff(x_cp, instant_center, z_force):
    x_ic, z_ic = instant_center
    return (z_force - z_ic) / (x_cp - x_ic)


def pitch_transfer(axle, fx, h_m, wheelbase_m):
    # Drive (+Fx) moves load from the front axle to the rear axle.
    return (fx if axle == "rear" else -fx) * h_m / wheelbase_m


def anti_pct(axle, coeff, h_m, wheelbase_m, fx=1.0):
    return 100.0 * coeff * fx / pitch_transfer(axle, fx, h_m, wheelbase_m)


def planar_coeff(links, x_cp, z_force):
    matrix = np.zeros((3, 3))
    for column, (chassis, upright) in enumerate(links):
        u = np.subtract(chassis, upright) / np.linalg.norm(np.subtract(chassis, upright))
        rx, rz = upright[0] - x_cp, upright[1]
        matrix[:, column] = (u[0], u[1], rz * u[0] - rx * u[1])
    matrix[:, 2] = (0.0, 1.0, 0.0)
    tensions_and_fz = np.linalg.solve(matrix, -np.array([1.0, 0.0, z_force]))
    return float(tensions_and_fz[2])


@dataclass(frozen=True)
class Corner:
    chassis: tuple[np.ndarray, ...]
    upright: tuple[np.ndarray, ...]
    r_cp: np.ndarray
    r_wc: np.ndarray
    spin: np.ndarray


def _unit(vector):
    return np.asarray(vector, dtype=float) / np.linalg.norm(vector)


def corner_from_vehicle(vehicle: Mapping, axle: str) -> Corner:
    side = vehicle[axle]
    radius = float(side["wheel"]["radius_m"])
    camber, toe = math.radians(side["wheel"]["camber_deg"]), math.radians(side["wheel"]["toe_deg"])
    r_wc = np.array(side["suspension"]["wheel_center_m"], dtype=float)
    r_cp = r_wc - radius * np.array([0.0, math.sin(camber), math.cos(camber)])
    heading = np.array([math.cos(toe), math.sin(toe), 0.0])
    return Corner(
        chassis=tuple(np.array(side[group][inboard], dtype=float) for group, inboard, _ in LINKS),
        upright=tuple(np.array(side["suspension"][outboard], dtype=float) for _, _, outboard in LINKS),
        r_cp=r_cp,
        r_wc=r_wc,
        spin=_unit(np.cross(heading, r_wc - r_cp)),
    )


def _directions(corner):
    return [_unit(chassis - upright) for chassis, upright in zip(corner.chassis, corner.upright)]


def _nonspin(corner, moment):
    return moment - np.dot(moment, corner.spin) * corner.spin


@dataclass(frozen=True)
class Split:
    upright_force: np.ndarray
    upright_moment: np.ndarray
    chassis_moment: np.ndarray
    spin_residual: float


def wrench_split(corner, force, torques, reactions):
    moment = np.cross(corner.r_cp - corner.r_wc, force)
    on = {place: sum(t for source, t in torques.items() if reactions[source] == place) for place in ("upright", "chassis")}
    return Split(
        upright_force=np.asarray(force, dtype=float),
        upright_moment=_nonspin(corner, moment) - on["upright"] * corner.spin,
        chassis_moment=-on["chassis"] * corner.spin,
        spin_residual=float(np.dot(moment, corner.spin) + on["upright"] + on["chassis"]),
    )


def closure_residual(corner, split, force, about):
    contact = np.cross(corner.r_cp - about, force)
    carried = np.cross(corner.r_wc - about, split.upright_force) + split.upright_moment + split.chassis_moment
    return np.concatenate((split.upright_force - force, carried - contact))


def spin_torque(corner, force):
    return -float(np.dot(np.cross(corner.r_cp - corner.r_wc, force), corner.spin))


def statics(corner, fx, reactions, given, balancing):
    arm = corner.r_cp - corner.r_wc
    matrix = np.zeros((7, 7))
    for column, (u, upright) in enumerate(zip(_directions(corner), corner.upright)):
        matrix[:3, column] = u
        matrix[3:6, column] = np.cross(upright - corner.r_wc, u)
    matrix[:3, 5] = Z
    matrix[3:6, 5] = _nonspin(corner, np.cross(arm, Z))
    matrix[6, 5] = np.dot(np.cross(arm, Z), corner.spin)
    matrix[3:6, 6] = -corner.spin if reactions[balancing] == "upright" else 0.0
    matrix[6, 6] = 1.0
    known = np.zeros(7)
    known[:3] = fx * X
    known[3:6] = fx * _nonspin(corner, np.cross(arm, X))
    known[6] = fx * np.dot(np.cross(arm, X), corner.spin)
    for source, torque in given.items():
        known[3:6] -= torque * corner.spin if reactions[source] == "upright" else 0.0
        known[6] += torque
    solution = np.linalg.solve(matrix, -known)
    return float(solution[5] / fx), solution[:5], float(solution[6])


def statics_coeff(corner, reaction, fx=1.0):
    return statics(corner, fx, {"source": reaction}, {}, "source")[0]


def twist(corner):
    matrix = np.zeros((6, 6))
    for row, (u, upright) in enumerate(zip(_directions(corner), corner.upright)):
        matrix[row, :3] = u
        matrix[row, 3:] = np.cross(upright - corner.r_cp, u)
    matrix[5, 2] = 1.0
    solution = np.linalg.solve(matrix, np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0]))
    return solution[:3], solution[3:]


def virtual_power_coeff(corner, reaction):
    v_cp, omega = twist(corner)
    arm = corner.r_cp - corner.r_wc

    def power(force):
        held = np.dot(np.cross(arm, force), corner.spin) * np.dot(corner.spin, omega) if reaction == "chassis" else 0.0
        return float(np.dot(force, v_cp) - held)

    return -power(X) / power(Z)
