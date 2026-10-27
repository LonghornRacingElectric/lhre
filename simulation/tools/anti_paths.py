import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from tools import vehicle_checks

SOURCES = ("drive", "hydraulic_brake", "regen")
AXLES = ("front", "rear")
REACTIONS = ("chassis", "upright")
AXLE_SIGN = {"front": -1.0, "rear": 1.0}
RELEASE_GRADE = ("measured", "cad_export", "declared")
NORMALIZATION = "per_axle_force"
COEFF_METHODS = {"upright": "contact_patch_path", "chassis": "virtual_power_3d"}
SIGN_CONVENTION = (
    "x forward, y left, z up. +Fx at the contact patch is drive. Jacking +Fz lifts the body. "
    "Anti is positive when the jacking opposes the pitch that the force causes."
)
RECORD_KEYS = (
    "axle",
    "source",
    "reaction",
    "coeff_n_per_n",
    "coeff_method",
    "axle_force_share",
    "normalization",
    "h_m",
    "h_basis",
    "wheelbase_m",
    "pose",
    "sign_convention",
    "pct",
    "status",
    "reason",
)
LINKS = (
    ("suspension", "lower_fore_i_m", "lower_o_m"),
    ("suspension", "lower_aft_i_m", "lower_o_m"),
    ("suspension", "upper_fore_i_m", "upper_o_m"),
    ("suspension", "upper_aft_i_m", "upper_o_m"),
    ("steering", "rack_pickup_m", "tie_o_m"),
)
LOCKED_SINGULAR_VALUE = 1e-9
X_AXIS = np.array([1.0, 0.0, 0.0])
Z_AXIS = np.array([0.0, 0.0, 1.0])


@dataclass(frozen=True)
class CornerCoefficients:
    axle: str
    values: Mapping[str, float]


@dataclass(frozen=True)
class AxlePath:
    share: float | None
    share_key: str
    share_status: str | None
    reaction: str
    reaction_key: str
    reaction_status: str | None


@dataclass(frozen=True)
class Normalization:
    h_m: float
    h_basis: str
    wheelbase_m: float
    h_reference: str
    datum_usable: bool
    datum_notes: tuple[str, ...]


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


def _twist(side: Mapping[str, Any]) -> np.ndarray:
    """Upright twist (v0, omega) at the design pose that keeps each link length fixed, for one unit of travel."""
    rows = []
    for group, inboard, outboard in LINKS:
        upright = np.asarray(side["suspension"][outboard], dtype=float)
        link = upright - np.asarray(side[group][inboard], dtype=float)
        rows.append(np.concatenate([link, np.cross(upright, link)]))
    _, singular, rows_t = np.linalg.svd(np.array(rows))
    if singular[-1] < LOCKED_SINGULAR_VALUE * singular[0]:
        raise ValueError("Two links are parallel, so the upright has more than one degree of freedom.")
    return rows_t[-1]


def corner_coefficients(vehicle: Mapping[str, Any], axle: str) -> CornerCoefficients:
    if axle not in AXLES:
        raise ValueError(f"Axle {axle!r} is not valid. Use front or rear.")
    side = vehicle[axle]
    radius = float(side["wheel"]["radius_m"])
    camber, toe = math.radians(side["wheel"]["camber_deg"]), math.radians(side["wheel"]["toe_deg"])
    center = np.asarray(side["suspension"]["wheel_center_m"], dtype=float)
    contact = center - radius * np.array([0.0, math.sin(camber), math.cos(camber)])
    spin = _unit(np.cross(np.array([math.cos(toe), math.sin(toe), 0.0]), center - contact))
    twist = _twist(side)
    v_origin, omega = twist[:3], twist[3:]
    t_cp, t_wc = v_origin + np.cross(omega, contact), v_origin + np.cross(omega, center)
    radius_vector = contact - center

    # The chassis holds the wheel spin, so the contact force does no work through the upright's spin rate.
    def power(force: np.ndarray) -> float:
        return float(force @ t_cp - np.dot(np.cross(radius_vector, force), spin) * np.dot(spin, omega))

    values = {
        "contact_patch_path": -float(t_cp[0] / t_cp[2]),
        "wheel_center_path": -float(t_wc[0] / t_cp[2]),
        "virtual_power_3d": -power(X_AXIS) / power(Z_AXIS),
    }
    return CornerCoefficients(axle=axle, values=values)


def anti_percent(axle: str, coeff_n_per_n: float, h_m: float, wheelbase_m: float) -> float:
    return AXLE_SIGN[axle] * 100.0 * coeff_n_per_n * wheelbase_m / h_m


def normalization(vehicle: Mapping[str, Any], frame: vehicle_checks.Frame) -> Normalization:
    parts = [vehicle["sprung_mass"]] + ([vehicle["driver_mass"]] if "driver_mass" in vehicle else [])
    mass = sum(float(part["mass_kg"]) for part in parts)
    x_cg = sum(float(part["mass_kg"]) * float(part["cg_m"][0]) for part in parts) / mass
    z_cg = sum(float(part["mass_kg"]) * float(part["cg_m"][2]) for part in parts) / mass
    x_front = float(vehicle["front"]["suspension"]["wheel_center_m"][0])
    x_rear = float(vehicle["rear"]["suspension"]["wheel_center_m"][0])
    wheelbase = x_front - x_rear
    datum = frame.datum_z
    usable = all(datum[axle].usable for axle in AXLES)
    notes = tuple(
        f"frame.datum_z.{axle} is {datum[axle].status}."
        for axle in AXLES
        if datum[axle].status not in RELEASE_GRADE
    )
    ground = 0.0
    if usable:
        ground = datum["rear"].value + (datum["front"].value - datum["rear"].value) * (x_cg - x_rear) / wheelbase
    if z_cg <= ground:
        raise ValueError(f"The CG z ({z_cg:g} m) is not above the ground plane ({ground:g} m) from frame.datum_z.")
    return Normalization(
        h_m=z_cg - ground,
        h_basis="sprung_and_driver_cg_z" if len(parts) == 2 else "sprung_cg_z",
        wheelbase_m=wheelbase,
        h_reference="ground plane from frame.datum_z" if usable else "z = 0 of the vehicle frame",
        datum_usable=usable,
        datum_notes=notes,
    )


def _leaf(node: Any, key: str) -> tuple[Any, Any]:
    leaf: Any = node
    for part in key.split("."):
        leaf = leaf.get(part) if isinstance(leaf, Mapping) else None
    if not (isinstance(leaf, Mapping) and {"value", "status"} <= set(leaf)):
        raise ValueError(f"The bundle has no {key} triple. Write an unknown value as status unavailable.")
    return leaf["value"], leaf["status"]


def axle_paths(vehicle: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[tuple[str, str], AxlePath]:
    brake = vehicle.get("brake")
    bias = brake.get("front_bias") if isinstance(brake, Mapping) else None
    paths = {}
    for source in SOURCES:
        for axle in AXLES:
            if source == "hydraulic_brake":
                reaction_key = f"torque_topology.hydraulic_brake.{axle}.reaction"
                share_key, status = "brake.front_bias", None
                share = None if bias is None else (float(bias) if axle == "front" else 1.0 - float(bias))
            else:
                reaction_key = f"torque_topology.{source}.reaction"
                share_key = f"torque_topology.{source}.{axle}_share"
                share, status = _leaf(bundle, share_key)
            reaction, reaction_status = _leaf(bundle, reaction_key)
            if reaction not in (*REACTIONS, None):
                raise ValueError(f"{reaction_key} is {reaction!r}. It must be chassis, upright or null.")
            paths[(source, axle)] = AxlePath(
                share=share,
                share_key=share_key,
                share_status=status,
                reaction=reaction or "unknown",
                reaction_key=reaction_key,
                reaction_status=reaction_status,
            )
    return paths


def _record(
    vehicle: Mapping[str, Any],
    axle: str,
    source: str,
    path: AxlePath,
    norm: Normalization,
    coefficients: CornerCoefficients | ValueError | None,
) -> dict[str, Any]:
    record: dict[str, Any] = dict.fromkeys(RECORD_KEYS)
    record.update(
        axle=axle,
        source=source,
        reaction=path.reaction,
        axle_force_share=path.share,
        normalization=NORMALIZATION,
        h_m=norm.h_m,
        h_basis=norm.h_basis,
        wheelbase_m=norm.wheelbase_m,
        pose={"kind": "hardpoint_twist", "jounce_m": 0.0, "rack_displacement_m": 0.0, "h_reference": norm.h_reference},
        sign_convention=SIGN_CONVENTION,
        status="unavailable",
    )
    if path.reaction == "unknown":
        record["reason"] = f"{path.reaction_key} is unavailable in the bundle. No reaction location is assumed."
        return record
    method = record["coeff_method"] = COEFF_METHODS[path.reaction]
    if not isinstance(coefficients, CornerCoefficients):
        record.update(status="invalid", reason=f"The {axle} hardpoints give no single-degree-of-freedom upright: {coefficients}")
        return record
    coeff = coefficients.values[method]
    record["pose"]["cross_checks_n_per_n"] = {"wheel_center_path": coefficients.values["wheel_center_path"]}
    if not math.isfinite(coeff):
        record.update(status="invalid", reason=f"The {method} coefficient is not finite.")
        return record
    record["coeff_n_per_n"] = coeff
    notes = [f"{path.reaction_key} is {path.reaction_status}."] if path.reaction_status not in RELEASE_GRADE else []
    if path.share_status is not None and path.share_status not in RELEASE_GRADE:
        notes.append(f"{path.share_key} is {path.share_status}.")
    if path.share == 0.0:
        notes.append("The axle force share is 0. The value is geometric only.")
    radii = {float(vehicle[name]["wheel"]["radius_m"]) for name in AXLES}
    if source == "hydraulic_brake" and len(radii) > 1:
        notes.append("brake.front_bias is a torque share. It is not the force share because the tire radii differ.")
    notes += norm.datum_notes
    if norm.datum_usable:
        record.update(pct=anti_percent(axle, coeff, norm.h_m, norm.wheelbase_m), status="ok")
    else:
        record["status"] = "partial"
        notes.append("The CG height above the ground is unknown, so pct is withheld. coeff_n_per_n does not use the datum.")
    record["reason"] = " ".join(notes) or None
    return record


def anti_records(vehicle: Mapping[str, Any], bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "force_paths" in vehicle:
        raise ValueError("vehicle.yml declares force_paths. lhre keeps the torque topology in vehicle/bundle.yml only.")
    frame = vehicle_checks.Frame.from_bundle(bundle)
    vehicle_checks.require(vehicle_checks.check_vehicle(vehicle, frame))
    paths = axle_paths(vehicle, bundle)
    norm = normalization(vehicle, frame)
    records = []
    for axle in AXLES:
        coefficients: CornerCoefficients | ValueError | None = None
        if any(paths[(source, axle)].reaction != "unknown" for source in SOURCES):
            try:
                coefficients = corner_coefficients(vehicle, axle)
            except ValueError as error:
                coefficients = error
        records += [_record(vehicle, axle, source, paths[(source, axle)], norm, coefficients) for source in SOURCES]
    return records
